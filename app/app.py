import hmac
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Flask, flash, get_flashed_messages, redirect,
                   render_template_string, request, session, url_for)
from markupsafe import Markup
from prometheus_client import Counter, Gauge
from prometheus_flask_exporter import PrometheusMetrics

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cle-de-demo-a-changer")
PrometheusMetrics(app)

DB_PATH = os.environ.get("DB_PATH", "/data/restaurant.db")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
RESTO = os.environ.get("RESTO_NOM", "Le Bon Goût")
TZ = timezone(timedelta(hours=int(os.environ.get("TZ_OFFSET", "1"))))

CATEGORIES = ["Entrées", "Plats", "Desserts", "Boissons"]
METHODES = {
    "especes": "Espèces",
    "carte": "Carte bancaire (simulation)",
    "mobile_money": "Mobile Money (simulation)",
}
STATUTS = {
    "en_attente_paiement": "En attente de paiement",
    "payee": "Payée",
    "en_preparation": "En préparation",
    "prete": "Prête",
    "servie": "Servie",
    "annulee": "Annulée",
}
SUIVANT = {"payee": "en_preparation", "en_preparation": "prete", "prete": "servie"}

# ---------- Métriques Prometheus ----------
g_ca = Gauge("restaurant_chiffre_affaires_fcfa", "Chiffre d'affaires total encaissé (FCFA)")
g_ca_jour = Gauge("restaurant_chiffre_affaires_jour_fcfa", "Chiffre d'affaires du jour (FCFA)")
g_payees = Gauge("restaurant_commandes_payees", "Nombre de commandes payées")
g_panier = Gauge("restaurant_panier_moyen_fcfa", "Panier moyen (FCFA)")
g_attente = Gauge("restaurant_commandes_en_attente_paiement", "Commandes en attente de paiement")
g_encours = Gauge("restaurant_commandes_en_cours", "Commandes payées pas encore servies")
c_paiements = Counter("restaurant_paiements", "Paiements reçus", ["methode"])
c_plats = Counter("restaurant_plats_vendus", "Plats vendus", ["plat"])


# ---------- Base de données ----------
def maintenant():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def tx():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with tx() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS plats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                categorie TEXT NOT NULL,
                prix INTEGER NOT NULL,
                stock INTEGER NOT NULL DEFAULT 0,
                actif INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS commandes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client TEXT NOT NULL,
                table_num INTEGER NOT NULL,
                statut TEXT NOT NULL,
                total INTEGER NOT NULL,
                methode TEXT,
                montant_recu INTEGER,
                monnaie INTEGER,
                cree_le TEXT NOT NULL,
                paye_le TEXT
            );
            CREATE TABLE IF NOT EXISTS lignes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                commande_id INTEGER NOT NULL REFERENCES commandes(id),
                plat_id INTEGER NOT NULL,
                nom TEXT NOT NULL,
                prix INTEGER NOT NULL,
                quantite INTEGER NOT NULL
            );
            """
        )
        if c.execute("SELECT COUNT(*) FROM plats").fetchone()[0] == 0:
            c.executemany(
                "INSERT INTO plats (nom, categorie, prix, stock) VALUES (?, ?, ?, ?)",
                [
                    ("Salade composée", "Entrées", 1500, 20),
                    ("Beignets haricots", "Entrées", 1000, 30),
                    ("Soupe de poisson", "Entrées", 2000, 15),
                    ("Ndolé et plantains", "Plats", 3500, 25),
                    ("Poulet DG", "Plats", 5000, 20),
                    ("Poisson braisé", "Plats", 4500, 15),
                    ("Riz sauté aux légumes", "Plats", 2500, 30),
                    ("Salade de fruits", "Desserts", 1500, 20),
                    ("Gâteau au chocolat", "Desserts", 2000, 12),
                    ("Eau minérale", "Boissons", 500, 60),
                    ("Jus de bissap", "Boissons", 800, 40),
                    ("Soda", "Boissons", 700, 50),
                ],
            )


def maj_metriques():
    jour = datetime.now(TZ).strftime("%Y-%m-%d")
    with tx() as c:
        r = c.execute(
            "SELECT COALESCE(SUM(total), 0) AS ca, COUNT(*) AS n "
            "FROM commandes WHERE paye_le IS NOT NULL"
        ).fetchone()
        j = c.execute(
            "SELECT COALESCE(SUM(total), 0) FROM commandes WHERE paye_le LIKE ?",
            (jour + "%",),
        ).fetchone()[0]
        attente = c.execute(
            "SELECT COUNT(*) FROM commandes WHERE statut = 'en_attente_paiement'"
        ).fetchone()[0]
        encours = c.execute(
            "SELECT COUNT(*) FROM commandes WHERE statut IN ('payee', 'en_preparation', 'prete')"
        ).fetchone()[0]
    g_ca.set(r["ca"])
    g_ca_jour.set(j)
    g_payees.set(r["n"])
    g_panier.set(r["ca"] / r["n"] if r["n"] else 0)
    g_attente.set(attente)
    g_encours.set(encours)


# ---------- Utilitaires ----------
@app.template_filter("fcfa")
def fcfa(n):
    return f"{int(n or 0):,}".replace(",", " ") + " FCFA"


def to_int(valeur, defaut=None):
    try:
        return int(str(valeur).strip())
    except (TypeError, ValueError):
        return defaut


def get_cart():
    return {int(k): int(v) for k, v in session.get("cart", {}).items()}


def save_cart(cart):
    session["cart"] = {str(k): v for k, v in cart.items() if v > 0}


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper


class CommandeError(Exception):
    pass


# ---------- Gabarits ----------
BASE = """
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} - {{ resto }}</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: Arial, sans-serif; margin: 0; color: #222; background: #faf8f5; }
  header { background: #7a1f1f; color: #fff; padding: 12px 20px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; }
  header a { color: #fff; text-decoration: none; margin-left: 14px; }
  header .brand { font-weight: bold; font-size: 20px; margin-left: 0; }
  main { max-width: 960px; margin: 20px auto; padding: 0 16px; }
  h1 { color: #7a1f1f; } h2 { color: #7a1f1f; border-bottom: 2px solid #e6d9c8; padding-bottom: 4px; margin-top: 28px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 12px; }
  .plat { background: #fff; border: 1px solid #e6d9c8; border-radius: 8px; padding: 12px; }
  .plat.epuise { opacity: .55; }
  .plat h3 { margin: 0 0 6px; font-size: 16px; }
  .prix { font-weight: bold; color: #7a1f1f; margin-bottom: 8px; }
  table { width: 100%; border-collapse: collapse; background: #fff; margin: 10px 0; }
  th, td { border: 1px solid #e0d6c8; padding: 8px; text-align: left; font-size: 14px; }
  th { background: #7a1f1f; color: #fff; }
  input, select { padding: 7px; border: 1px solid #bbb; border-radius: 4px; }
  button, .btn { background: #7a1f1f; color: #fff; border: 0; padding: 8px 14px; border-radius: 4px; cursor: pointer; text-decoration: none; display: inline-block; font-size: 14px; }
  button.sec { background: #666; } button.ok { background: #1f7a3a; }
  .flash { padding: 10px 14px; border-radius: 6px; margin-bottom: 12px; }
  .flash.ok { background: #d9f2e0; } .flash.err { background: #fde2e2; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 14px 0; }
  .card { background: #fff; border: 1px solid #e6d9c8; border-radius: 8px; padding: 14px; }
  .card b { display: block; font-size: 22px; color: #7a1f1f; margin-top: 4px; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; background: #eee; font-size: 12px; }
  .badge.payee, .badge.servie { background: #d9f2e0; } .badge.en_attente_paiement { background: #fff0c2; }
  .badge.en_preparation, .badge.prete { background: #dbe8ff; } .badge.annulee { background: #fde2e2; }
  form.inline { display: inline; }
  .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  footer { text-align: center; color: #888; padding: 24px; font-size: 13px; }
</style>
</head>
<body>
<header>
  <a class="brand" href="{{ url_for('menu') }}">{{ resto }}</a>
  <nav>
    <a href="{{ url_for('menu') }}">Menu</a>
    <a href="{{ url_for('panier') }}">Panier ({{ nb_panier }})</a>
    <a href="{{ url_for('admin') }}">Gestion</a>
    {% if admin %}<a href="{{ url_for('admin_deconnexion') }}">Déconnexion</a>{% endif %}
  </nav>
</header>
<main>
  {% for cat, msg in get_flashed_messages(with_categories=true) %}
    <div class="flash {{ cat }}">{{ msg }}</div>
  {% endfor %}
  {{ body }}
</main>
<footer>{{ resto }} - Version 1</footer>
</body>
</html>
"""

MENU = """
<h1>Notre menu</h1>
{% for cat, plats in groupes %}
  <h2>{{ cat }}</h2>
  <div class="grid">
  {% for p in plats %}
    <div class="plat {{ 'epuise' if p['stock'] <= 0 else '' }}">
      <h3>{{ p['nom'] }}</h3>
      <div class="prix">{{ p['prix']|fcfa }}</div>
      {% if p['stock'] <= 0 %}
        <span class="badge">Épuisé</span>
      {% else %}
        <form method="post" action="{{ url_for('panier_ajouter', pid=p['id']) }}" class="row">
          <input type="number" name="quantite" value="1" min="1" max="{{ [p['stock'], 20]|min }}" style="width:64px">
          <button>Ajouter</button>
        </form>
        <small>{{ p['stock'] }} disponible(s)</small>
      {% endif %}
    </div>
  {% endfor %}
  </div>
{% endfor %}
"""

PANIER = """
<h1>Nos paniers</h1>
{% if lignes %}
<table>
  <tr><th>Plat</th><th>Prix</th><th>Quantité</th><th>Sous-total</th></tr>
  {% for l in lignes %}
  <tr>
    <td>{{ l['nom'] }}</td>
    <td>{{ l['prix']|fcfa }}</td>
    <td>
      <form method="post" action="{{ url_for('panier_modifier', pid=l['id']) }}" class="row">
        <input type="number" name="quantite" value="{{ l['quantite'] }}" min="0" style="width:64px">
        <button class="sec">Mettre à jour</button>
      </form>
    </td>
    <td>{{ l['sous_total']|fcfa }}</td>
  </tr>
  {% endfor %}
  <tr><th colspan="3">Total à payer</th><th>{{ total|fcfa }}</th></tr>
</table>
<h2>Valider la commande</h2>
<form method="post" action="{{ url_for('commander') }}" class="row">
  <input name="client" placeholder="Votre nom" maxlength="60" required>
  <input name="table_num" type="number" min="1" max="99" placeholder="N° de table" required style="width:120px">
  <button class="ok">Commander</button>
</form>
{% else %}
<p>Votre panier est vide. <a href="{{ url_for('menu') }}">Voir le menu</a></p>
{% endif %}
"""

COMMANDE = """
<h1>Commande n° {{ c['id'] }}</h1>
<p>Client : <b>{{ c['client'] }}</b> - Table <b>{{ c['table_num'] }}</b> -
   <span class="badge {{ c['statut'] }}">{{ statuts[c['statut']] }}</span></p>
<table>
  <tr><th>Plat</th><th>Prix</th><th>Qté</th><th>Sous-total</th></tr>
  {% for l in lignes %}
  <tr><td>{{ l['nom'] }}</td><td>{{ l['prix']|fcfa }}</td><td>{{ l['quantite'] }}</td><td>{{ (l['prix'] * l['quantite'])|fcfa }}</td></tr>
  {% endfor %}
  <tr><th colspan="3">Total</th><th>{{ c['total']|fcfa }}</th></tr>
</table>

{% if c['statut'] == 'en_attente_paiement' %}
  <h2>Paiement</h2>
  <form method="post" action="{{ url_for('payer', cid=c['id']) }}">
    <p>
      {% for k, v in methodes.items() %}
        <label><input type="radio" name="methode" value="{{ k }}" {{ 'checked' if loop.first else '' }}> {{ v }}</label><br>
      {% endfor %}
    </p>
    <p>Espèces uniquement - montant remis :
      <input id="recu" name="montant_recu" type="number" min="0" placeholder="{{ c['total'] }}">
      <b id="monnaie"></b>
    </p>
    <button class="ok">Payer {{ c['total']|fcfa }}</button>
  </form>
  <form method="post" action="{{ url_for('annuler_client', cid=c['id']) }}" style="margin-top:10px">
    <button class="sec">Annuler la commande</button>
  </form>
  <script>
    const total = {{ c['total'] }};
    const inp = document.getElementById('recu'), out = document.getElementById('monnaie');
    inp.addEventListener('input', () => {
      const m = (parseInt(inp.value) || 0) - total;
      out.textContent = m >= 0 ? 'Monnaie à rendre : ' + m + ' FCFA' : 'Montant insuffisant';
    });
  </script>
{% elif c['statut'] != 'annulee' %}
  <h2>Reçu de paiement</h2>
  <p>Payé le {{ c['paye_le'] }} par <b>{{ methodes[c['methode']] }}</b>.</p>
  {% if c['methode'] == 'especes' %}
    <p>Montant remis : {{ c['montant_recu']|fcfa }} - Monnaie rendue : <b>{{ c['monnaie']|fcfa }}</b></p>
  {% endif %}
  <p>Merci pour votre commande !</p>
  {% if c['statut'] != 'servie' %}<script>setTimeout(() => location.reload(), 15000);</script>{% endif %}
{% endif %}
"""

LOGIN = """
<h1>Espace gestion</h1>
<form method="post" class="row">
  <input type="password" name="mot_de_passe" placeholder="Mot de passe" required>
  <button>Se connecter</button>
</form>
"""

ADMIN = """
<h1>Tableau de bord</h1>
<div class="stats">
  <div class="card">Chiffre d'affaires total<b>{{ s['ca']|fcfa }}</b></div>
  <div class="card">CA du jour<b>{{ s['ca_jour']|fcfa }}</b></div>
  <div class="card">Commandes payées<b>{{ s['n'] }}</b></div>
  <div class="card">Panier moyen<b>{{ s['panier']|fcfa }}</b></div>
  <div class="card">En attente de paiement<b>{{ s['attente'] }}</b></div>
  <div class="card">En cours<b>{{ s['encours'] }}</b></div>
</div>
<p><a class="btn" href="{{ url_for('admin_menu') }}">Gérer le menu</a></p>

<h2>Plats les plus vendus</h2>
<table>
  <tr><th>Plat</th><th>Quantité vendue</th><th>Chiffre d'affaires</th></tr>
  {% for t in top %}<tr><td>{{ t['nom'] }}</td><td>{{ t['q'] }}</td><td>{{ t['ca']|fcfa }}</td></tr>
  {% else %}<tr><td colspan="3">Aucune vente pour le moment.</td></tr>{% endfor %}
</table>

<h2>Commandes récentes</h2>
<table>
  <tr><th>N°</th><th>Client / table</th><th>Détail</th><th>Total</th><th>Statut</th><th>Paiement</th><th>Actions</th></tr>
  {% for c in commandes %}
  <tr>
    <td>{{ c['id'] }}</td>
    <td>{{ c['client'] }} (table {{ c['table_num'] }})</td>
    <td>{{ c['detail'] }}</td>
    <td>{{ c['total']|fcfa }}</td>
    <td><span class="badge {{ c['statut'] }}">{{ statuts[c['statut']] }}</span></td>
    <td>{{ methodes[c['methode']] if c['methode'] else '-' }}</td>
    <td>
      {% if c['statut'] in suivant %}
        <form class="inline" method="post" action="{{ url_for('admin_statut', cid=c['id']) }}">
          <button class="ok">Passer à : {{ statuts[suivant[c['statut']]] }}</button>
        </form>
      {% elif c['statut'] == 'en_attente_paiement' %}
        <form class="inline" method="post" action="{{ url_for('admin_annuler', cid=c['id']) }}">
          <button class="sec">Annuler</button>
        </form>
      {% endif %}
    </td>
  </tr>
  {% else %}<tr><td colspan="7">Aucune commande.</td></tr>{% endfor %}
</table>
<script>setTimeout(() => location.reload(), 20000);</script>
"""

ADMIN_MENU = """
<h1>Gestion du menu</h1>
<h2>Ajouter un plat</h2>
<form method="post" action="{{ url_for('admin_menu_ajouter') }}" class="row">
  <input name="nom" placeholder="Nom du plat" required maxlength="80">
  <select name="categorie">{% for c in categories %}<option>{{ c }}</option>{% endfor %}</select>
  <input name="prix" type="number" min="0" placeholder="Prix (FCFA)" required style="width:130px">
  <input name="stock" type="number" min="0" placeholder="Stock" required style="width:90px">
  <button>Ajouter</button>
</form>
<h2>Plats existants</h2>
<table>
  <tr><th>Plat</th><th>Catégorie</th><th>Prix (FCFA)</th><th>Stock</th><th>Visible</th><th></th></tr>
  {% for p in plats %}
  <tr>
    <form method="post" action="{{ url_for('admin_menu_modifier', pid=p['id']) }}">
      <td>{{ p['nom'] }}</td><td>{{ p['categorie'] }}</td>
      <td><input name="prix" type="number" min="0" value="{{ p['prix'] }}" style="width:100px"></td>
      <td><input name="stock" type="number" min="0" value="{{ p['stock'] }}" style="width:80px"></td>
      <td><input type="checkbox" name="actif" {{ 'checked' if p['actif'] else '' }}></td>
      <td><button>Enregistrer</button></td>
    </form>
  </tr>
  {% endfor %}
</table>
<p><a class="btn sec" href="{{ url_for('admin') }}">Retour au tableau de bord</a></p>
"""


def page(title, template, **ctx):
    corps = render_template_string(
        template, statuts=STATUTS, methodes=METHODES, suivant=SUIVANT, **ctx
    )
    return render_template_string(
        BASE, title=title, body=Markup(corps), resto=RESTO,
        nb_panier=sum(get_cart().values()), admin=session.get("admin"),
    )


# ---------- Côté client ----------
@app.route("/")
def menu():
    with tx() as c:
        plats = c.execute("SELECT * FROM plats WHERE actif = 1 ORDER BY id").fetchall()
    groupes = [(cat, [p for p in plats if p["categorie"] == cat]) for cat in CATEGORIES]
    return page("Menu", MENU, groupes=[g for g in groupes if g[1]])


@app.route("/panier")
def panier():
    cart = get_cart()
    lignes, total = [], 0
    with tx() as c:
        for pid, qte in cart.items():
            p = c.execute("SELECT * FROM plats WHERE id = ?", (pid,)).fetchone()
            if p:
                st = p["prix"] * qte
                total += st
                lignes.append({"id": pid, "nom": p["nom"], "prix": p["prix"],
                               "quantite": qte, "sous_total": st})
    return page("Panier", PANIER, lignes=lignes, total=total)


@app.route("/panier/ajouter/<int:pid>", methods=["POST"])
def panier_ajouter(pid):
    qte = to_int(request.form.get("quantite"), 1)
    qte = max(1, min(qte, 20))
    cart = get_cart()
    with tx() as c:
        p = c.execute("SELECT * FROM plats WHERE id = ? AND actif = 1", (pid,)).fetchone()
    if not p:
        flash("Ce plat n'existe pas.", "err")
    elif cart.get(pid, 0) + qte > p["stock"]:
        flash(f"Stock insuffisant pour « {p['nom']} » ({p['stock']} disponible(s)).", "err")
    else:
        cart[pid] = cart.get(pid, 0) + qte
        save_cart(cart)
        flash(f"{qte} x {p['nom']} ajouté au panier.", "ok")
    return redirect(url_for("menu"))


@app.route("/panier/modifier/<int:pid>", methods=["POST"])
def panier_modifier(pid):
    qte = to_int(request.form.get("quantite"), 0)
    cart = get_cart()
    with tx() as c:
        p = c.execute("SELECT * FROM plats WHERE id = ?", (pid,)).fetchone()
    if p and qte > p["stock"]:
        flash(f"Stock insuffisant pour « {p['nom']} » ({p['stock']} disponible(s)).", "err")
    else:
        cart[pid] = max(0, qte)
        save_cart(cart)
    return redirect(url_for("panier"))


@app.route("/commander", methods=["POST"])
def commander():
    cart = get_cart()
    client = request.form.get("client", "").strip()[:60]
    table = to_int(request.form.get("table_num"))
    if not cart or not client or table is None or not 1 <= table <= 99:
        flash("Panier vide, nom manquant ou numéro de table invalide.", "err")
        return redirect(url_for("panier"))
    try:
        with tx() as c:
            lignes, total = [], 0
            for pid, qte in cart.items():
                p = c.execute("SELECT * FROM plats WHERE id = ? AND actif = 1", (pid,)).fetchone()
                if not p:
                    raise CommandeError("Un plat du panier n'est plus disponible.")
                ok = c.execute(
                    "UPDATE plats SET stock = stock - ? WHERE id = ? AND stock >= ?",
                    (qte, pid, qte),
                ).rowcount
                if not ok:
                    raise CommandeError(f"Stock insuffisant pour « {p['nom']} ».")
                total += p["prix"] * qte
                lignes.append((pid, p["nom"], p["prix"], qte))
            cur = c.execute(
                "INSERT INTO commandes (client, table_num, statut, total, cree_le) "
                "VALUES (?, ?, 'en_attente_paiement', ?, ?)",
                (client, table, total, maintenant()),
            )
            cid = cur.lastrowid
            c.executemany(
                "INSERT INTO lignes (commande_id, plat_id, nom, prix, quantite) VALUES (?, ?, ?, ?, ?)",
                [(cid, *l) for l in lignes],
            )
    except CommandeError as e:
        flash(str(e), "err")
        return redirect(url_for("panier"))
    save_cart({})
    maj_metriques()
    return redirect(url_for("commande", cid=cid))


def charger_commande(cid):
    with tx() as c:
        cmd = c.execute("SELECT * FROM commandes WHERE id = ?", (cid,)).fetchone()
        lignes = c.execute("SELECT * FROM lignes WHERE commande_id = ?", (cid,)).fetchall()
    return cmd, lignes


@app.route("/commande/<int:cid>")
def commande(cid):
    cmd, lignes = charger_commande(cid)
    if not cmd:
        flash("Commande introuvable.", "err")
        return redirect(url_for("menu"))
    return page(f"Commande {cid}", COMMANDE, c=cmd, lignes=lignes)


@app.route("/commande/<int:cid>/payer", methods=["POST"])
def payer(cid):
    methode = request.form.get("methode")
    if methode not in METHODES:
        flash("Choisissez un mode de paiement.", "err")
        return redirect(url_for("commande", cid=cid))
    with tx() as c:
        cmd = c.execute("SELECT * FROM commandes WHERE id = ?", (cid,)).fetchone()
        if not cmd or cmd["statut"] != "en_attente_paiement":
            flash("Cette commande ne peut pas être payée.", "err")
            return redirect(url_for("commande", cid=cid))
        recu, monnaie = cmd["total"], 0
        if methode == "especes":
            recu = to_int(request.form.get("montant_recu"))
            if recu is None or recu < cmd["total"]:
                flash("Montant remis insuffisant pour payer en espèces.", "err")
                return redirect(url_for("commande", cid=cid))
            monnaie = recu - cmd["total"]
        ok = c.execute(
            "UPDATE commandes SET statut = 'payee', methode = ?, montant_recu = ?, "
            "monnaie = ?, paye_le = ? WHERE id = ? AND statut = 'en_attente_paiement'",
            (methode, recu, monnaie, maintenant(), cid),
        ).rowcount
        if not ok:
            flash("Cette commande a déjà été traitée.", "err")
            return redirect(url_for("commande", cid=cid))
        lignes = c.execute("SELECT nom, quantite FROM lignes WHERE commande_id = ?", (cid,)).fetchall()
    c_paiements.labels(methode).inc()
    for l in lignes:
        c_plats.labels(l["nom"]).inc(l["quantite"])
    maj_metriques()
    flash("Paiement accepté, merci !", "ok")
    return redirect(url_for("commande", cid=cid))


def annuler(cid):
    with tx() as c:
        ok = c.execute(
            "UPDATE commandes SET statut = 'annulee' WHERE id = ? AND statut = 'en_attente_paiement'",
            (cid,),
        ).rowcount
        if ok:
            for l in c.execute("SELECT plat_id, quantite FROM lignes WHERE commande_id = ?", (cid,)).fetchall():
                c.execute("UPDATE plats SET stock = stock + ? WHERE id = ?", (l["quantite"], l["plat_id"]))
    maj_metriques()
    return bool(ok)


@app.route("/commande/<int:cid>/annuler", methods=["POST"])
def annuler_client(cid):
    flash("Commande annulée." if annuler(cid) else "Annulation impossible.", "ok")
    return redirect(url_for("commande", cid=cid))


# ---------- Côté gestion ----------
@app.route("/admin/connexion", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        saisi = request.form.get("mot_de_passe", "")
        if hmac.compare_digest(saisi.encode(), ADMIN_PASSWORD.encode()):
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("Mot de passe incorrect.", "err")
    return page("Connexion", LOGIN)


@app.route("/admin/deconnexion")
def admin_deconnexion():
    session.pop("admin", None)
    return redirect(url_for("menu"))


@app.route("/admin")
@admin_required
def admin():
    jour = datetime.now(TZ).strftime("%Y-%m-%d")
    with tx() as c:
        r = c.execute(
            "SELECT COALESCE(SUM(total), 0) AS ca, COUNT(*) AS n FROM commandes WHERE paye_le IS NOT NULL"
        ).fetchone()
        s = {
            "ca": r["ca"],
            "n": r["n"],
            "panier": round(r["ca"] / r["n"]) if r["n"] else 0,
            "ca_jour": c.execute(
                "SELECT COALESCE(SUM(total), 0) FROM commandes WHERE paye_le LIKE ?", (jour + "%",)
            ).fetchone()[0],
            "attente": c.execute(
                "SELECT COUNT(*) FROM commandes WHERE statut = 'en_attente_paiement'"
            ).fetchone()[0],
            "encours": c.execute(
                "SELECT COUNT(*) FROM commandes WHERE statut IN ('payee', 'en_preparation', 'prete')"
            ).fetchone()[0],
        }
        top = c.execute(
            "SELECT l.nom AS nom, SUM(l.quantite) AS q, SUM(l.quantite * l.prix) AS ca "
            "FROM lignes l JOIN commandes c ON c.id = l.commande_id "
            "WHERE c.paye_le IS NOT NULL GROUP BY l.nom ORDER BY q DESC LIMIT 5"
        ).fetchall()
        commandes = c.execute(
            "SELECT c.*, (SELECT group_concat(quantite || ' x ' || nom, ', ') "
            "FROM lignes WHERE commande_id = c.id) AS detail "
            "FROM commandes c ORDER BY c.id DESC LIMIT 40"
        ).fetchall()
    return page("Tableau de bord", ADMIN, s=s, top=top, commandes=commandes)


@app.route("/admin/commande/<int:cid>/statut", methods=["POST"])
@admin_required
def admin_statut(cid):
    with tx() as c:
        cmd = c.execute("SELECT statut FROM commandes WHERE id = ?", (cid,)).fetchone()
        if cmd and cmd["statut"] in SUIVANT:
            c.execute("UPDATE commandes SET statut = ? WHERE id = ?", (SUIVANT[cmd["statut"]], cid))
    maj_metriques()
    return redirect(url_for("admin"))


@app.route("/admin/commande/<int:cid>/annuler", methods=["POST"])
@admin_required
def admin_annuler(cid):
    annuler(cid)
    return redirect(url_for("admin"))


@app.route("/admin/menu")
@admin_required
def admin_menu():
    with tx() as c:
        plats = c.execute("SELECT * FROM plats ORDER BY id").fetchall()
    return page("Gestion du menu", ADMIN_MENU, plats=plats, categories=CATEGORIES)


@app.route("/admin/menu/ajouter", methods=["POST"])
@admin_required
def admin_menu_ajouter():
    nom = request.form.get("nom", "").strip()[:80]
    cat = request.form.get("categorie")
    prix, stock = to_int(request.form.get("prix")), to_int(request.form.get("stock"))
    if nom and cat in CATEGORIES and prix is not None and prix >= 0 and stock is not None and stock >= 0:
        with tx() as c:
            c.execute("INSERT INTO plats (nom, categorie, prix, stock) VALUES (?, ?, ?, ?)",
                      (nom, cat, prix, stock))
        flash("Plat ajouté.", "ok")
    else:
        flash("Données invalides.", "err")
    return redirect(url_for("admin_menu"))


@app.route("/admin/menu/<int:pid>", methods=["POST"])
@admin_required
def admin_menu_modifier(pid):
    prix, stock = to_int(request.form.get("prix")), to_int(request.form.get("stock"))
    if prix is None or stock is None or prix < 0 or stock < 0:
        flash("Données invalides.", "err")
    else:
        with tx() as c:
            c.execute("UPDATE plats SET prix = ?, stock = ?, actif = ? WHERE id = ?",
                      (prix, stock, 1 if request.form.get("actif") else 0, pid))
        flash("Plat mis à jour.", "ok")
    return redirect(url_for("admin_menu"))


@app.route("/health")
def health():
    return {"status": "ok"}


init_db()
maj_metriques()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)