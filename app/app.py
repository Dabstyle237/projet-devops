import os
import sqlite3

from flask import Flask, redirect, render_template_string, request, url_for
from prometheus_client import Gauge
from prometheus_flask_exporter import PrometheusMetrics

app = Flask(__name__)
PrometheusMetrics(app)

DB_PATH = os.environ.get("DB_PATH", "/data/stock.db")

nb_produits = Gauge("stock_nombre_produits", "Nombre de produits en stock")
valeur_stock = Gauge("stock_valeur_totale", "Valeur totale du stock")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_db() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS produits (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   nom TEXT NOT NULL,
                   quantite INTEGER NOT NULL DEFAULT 0,
                   prix REAL NOT NULL DEFAULT 0
               )"""
        )


def maj_metriques():
    with get_db() as db:
        r = db.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(quantite * prix), 0) AS v FROM produits"
        ).fetchone()
    nb_produits.set(r["n"])
    valeur_stock.set(r["v"])


PAGE = """
<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Gestion de stock</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 860px; margin: 30px auto; padding: 0 16px; color: #222; }
    h1 { color: #1F3864; }
    table { width: 100%; border-collapse: collapse; margin-top: 16px; }
    th, td { border: 1px solid #ccc; padding: 8px; text-align: left; }
    th { background: #1F3864; color: #fff; }
    form.add { display: flex; gap: 8px; flex-wrap: wrap; }
    input { padding: 7px; }
    button { padding: 7px 12px; cursor: pointer; }
    .low { background: #fde2e2; }
    .stats { display: flex; gap: 16px; margin: 16px 0; }
    .card { background: #f2f5fb; padding: 12px 18px; border-radius: 6px; }
    .inline { display: inline; }
  </style>
</head>
<body>
  <h1>Gestion de stock</h1>
  <div class="stats">
    <div class="card"><b>{{ produits|length }}</b> produit(s)</div>
    <div class="card">Valeur du stock : <b>{{ "%.2f"|format(valeur) }}</b></div>
  </div>

  <form class="add" method="post" action="{{ url_for('ajouter') }}">
    <input name="nom" placeholder="Nom du produit" required>
    <input name="quantite" type="number" min="0" placeholder="Quantité" required>
    <input name="prix" type="number" min="0" step="0.01" placeholder="Prix unitaire" required>
    <button type="submit">Ajouter</button>
  </form>

  <table>
    <tr><th>#</th><th>Produit</th><th>Quantité</th><th>Prix</th><th>Actions</th></tr>
    {% for p in produits %}
    <tr class="{{ 'low' if p['quantite'] < 5 else '' }}">
      <td>{{ p['id'] }}</td>
      <td>{{ p['nom'] }}</td>
      <td>
        <form class="inline" method="post" action="{{ url_for('modifier', pid=p['id']) }}">
          <input name="quantite" type="number" min="0" value="{{ p['quantite'] }}" style="width:70px">
          <button type="submit">OK</button>
        </form>
      </td>
      <td>{{ "%.2f"|format(p['prix']) }}</td>
      <td>
        <form class="inline" method="post" action="{{ url_for('supprimer', pid=p['id']) }}">
          <button type="submit">Supprimer</button>
        </form>
      </td>
    </tr>
    {% else %}
    <tr><td colspan="5">Aucun produit pour le moment.</td></tr>
    {% endfor %}
  </table>
  <p><small>Lignes rouges : stock inférieur à 5. Version 1.</small></p>
</body>
</html>
"""


@app.route("/")
def index():
    with get_db() as db:
        produits = db.execute("SELECT * FROM produits ORDER BY id DESC").fetchall()
    valeur = sum(p["quantite"] * p["prix"] for p in produits)
    return render_template_string(PAGE, produits=produits, valeur=valeur)


@app.route("/ajouter", methods=["POST"])
def ajouter():
    nom = request.form.get("nom", "").strip()
    if nom:
        with get_db() as db:
            db.execute(
                "INSERT INTO produits (nom, quantite, prix) VALUES (?, ?, ?)",
                (nom, int(request.form.get("quantite", 0)), float(request.form.get("prix", 0))),
            )
        maj_metriques()
    return redirect(url_for("index"))


@app.route("/modifier/<int:pid>", methods=["POST"])
def modifier(pid):
    with get_db() as db:
        db.execute(
            "UPDATE produits SET quantite = ? WHERE id = ?",
            (int(request.form.get("quantite", 0)), pid),
        )
    maj_metriques()
    return redirect(url_for("index"))


@app.route("/supprimer/<int:pid>", methods=["POST"])
def supprimer(pid):
    with get_db() as db:
        db.execute("DELETE FROM produits WHERE id = ?", (pid,))
    maj_metriques()
    return redirect(url_for("index"))


@app.route("/health")
def health():
    return {"status": "ok"}


init_db()
maj_metriques()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)