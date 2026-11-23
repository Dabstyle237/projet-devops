import os
import sqlite3
from flask import Flask, redirect, render_template_string, request, url_for
from prometheus_client import Gauge
from prometheus_flask_exporter import PrometheusMetrics

app = Flask(__name__)

# Prometheus
PrometheusMetrics(app)

# Base de données
DB_PATH = os.environ.get("DB_PATH", "/data/restaurant.db")

# =========================
# METRIQUES PROMETHEUS
# =========================

nb_commandes = Gauge(
    "restaurant_nombre_commandes",
    "Nombre total de commandes"
)

chiffre_affaires = Gauge(
    "restaurant_chiffre_affaires",
    "Chiffre d'affaires total"
)

nb_plats = Gauge(
    "restaurant_nombre_plats",
    "Nombre de plats"
)

nb_tables_occupees = Gauge(
    "restaurant_tables_occupees",
    "Nombre de tables actuellement occupees"
)

# =========================
# BASE DE DONNEES
# =========================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    with get_db() as db:

        # Plats
        db.execute("""
            CREATE TABLE IF NOT EXISTS plats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nom TEXT NOT NULL,
                categorie TEXT NOT NULL,
                prix REAL NOT NULL,
                disponible INTEGER DEFAULT 1
            )
        """)

        # Tables
        db.execute("""
            CREATE TABLE IF NOT EXISTS tables_restaurant (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero INTEGER NOT NULL UNIQUE,
                capacite INTEGER NOT NULL,
                statut TEXT DEFAULT 'Libre'
            )
        """)

        # Commandes
        db.execute("""
            CREATE TABLE IF NOT EXISTS commandes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                table_id INTEGER,
                client TEXT NOT NULL,
                total REAL DEFAULT 0,
                statut TEXT DEFAULT 'En attente',
                date_commande TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(table_id)
                    REFERENCES tables_restaurant(id)
            )
        """)

        # Lignes de commandes
        db.execute("""
            CREATE TABLE IF NOT EXISTS lignes_commande (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                commande_id INTEGER NOT NULL,
                plat_id INTEGER NOT NULL,
                quantite INTEGER NOT NULL,
                prix REAL NOT NULL,
                FOREIGN KEY(commande_id)
                    REFERENCES commandes(id),
                FOREIGN KEY(plat_id)
                    REFERENCES plats(id)
            )
        """)

        # Quelques plats par défaut
        count = db.execute(
            "SELECT COUNT(*) AS n FROM plats"
        ).fetchone()["n"]

        if count == 0:

            db.executemany("""
                INSERT INTO plats
                (nom, categorie, prix, disponible)
                VALUES (?, ?, ?, ?)
            """, [

                ("Poulet braisé", "Plats", 3500, 1),
                ("Poisson braisé", "Plats", 4000, 1),
                ("Spaghetti bolognaise", "Plats", 3000, 1),
                ("Riz sauté", "Plats", 2500, 1),
                ("Salade composée", "Entrées", 1500, 1),
                ("Jus naturel", "Boissons", 1000, 1),
                ("Eau minérale", "Boissons", 500, 1),
                ("Tiramisu", "Desserts", 2000, 1)

            ])

        # Tables par défaut

        count_tables = db.execute(
            "SELECT COUNT(*) AS n FROM tables_restaurant"
        ).fetchone()["n"]

        if count_tables == 0:

            for numero in range(1, 11):

                capacite = 4

                db.execute("""
                    INSERT INTO tables_restaurant
                    (numero, capacite, statut)
                    VALUES (?, ?, ?)
                """, (numero, capacite, "Libre"))


# =========================
# METRIQUES
# =========================

def update_metrics():

    with get_db() as db:

        commandes = db.execute("""
            SELECT COUNT(*) AS n
            FROM commandes
        """).fetchone()["n"]

        ca = db.execute("""
            SELECT COALESCE(SUM(total), 0) AS total
            FROM commandes
            WHERE statut = 'Payée'
        """).fetchone()["total"]

        plats = db.execute("""
            SELECT COUNT(*) AS n
            FROM plats
        """).fetchone()["n"]

        tables = db.execute("""
            SELECT COUNT(*) AS n
            FROM tables_restaurant
            WHERE statut = 'Occupée'
        """).fetchone()["n"]

    nb_commandes.set(commandes)
    chiffre_affaires.set(ca)
    nb_plats.set(plats)
    nb_tables_occupees.set(tables)


# =========================
# INTERFACE
# =========================

PAGE = """

<!DOCTYPE html>

<html lang="fr">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Dabstyle Restaurant</title>

<style>

body {
    font-family: Arial, sans-serif;
    margin: 0;
    background: #f4f6f8;
    color: #222;
}

header {
    background: #1f3864;
    color: white;
    padding: 20px;
}

header h1 {
    margin: 0;
}

nav {
    margin-top: 15px;
}

nav a {
    color: white;
    text-decoration: none;
    margin-right: 20px;
    font-weight: bold;
}

.container {
    max-width: 1200px;
    margin: 30px auto;
    padding: 0 20px;
}

.cards {
    display: grid;
    grid-template-columns:
    repeat(auto-fit, minmax(200px, 1fr));

    gap: 20px;
}

.card {
    background: white;
    padding: 20px;
    border-radius: 10px;
    box-shadow:
    0 2px 8px rgba(0,0,0,0.08);
}

.card h3 {
    margin-top: 0;
}

.card .number {
    font-size: 30px;
    font-weight: bold;
}

.section {
    background: white;
    margin-top: 30px;
    padding: 20px;
    border-radius: 10px;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 15px;
}

th, td {
    padding: 12px;
    border-bottom: 1px solid #ddd;
    text-align: left;
}

th {
    background: #1f3864;
    color: white;
}

button {
    padding: 8px 12px;
    border: none;
    border-radius: 5px;
    cursor: pointer;
}

.btn {
    background: #1f3864;
    color: white;
}

.btn-danger {
    background: #c0392b;
    color: white;
}

.btn-success {
    background: #27ae60;
    color: white;
}

form {
    display: inline;
}

input, select {
    padding: 9px;
    margin: 5px;
    border: 1px solid #ccc;
    border-radius: 5px;
}

.badge {
    padding: 5px 10px;
    border-radius: 15px;
    background: #eee;
}

</style>

</head>

<body>

<header>

<h1>🍽️ Dabstyle Restaurant</h1>

<nav>

<a href="/">Dashboard</a>

<a href="/plats">Plats</a>

<a href="/tables">Tables</a>

<a href="/commandes">Commandes</a>

</nav>

</header>


<div class="container">

<!-- DASHBOARD -->

<h2>Tableau de bord</h2>

<div class="cards">

<div class="card">

<h3>Commandes</h3>

<div class="number">
{{ stats.commandes }}
</div>

</div>


<div class="card">

<h3>Chiffre d'affaires</h3>

<div class="number">
{{ "%.0f"|format(stats.ca) }} FCFA
</div>

</div>


<div class="card">

<h3>Plats</h3>

<div class="number">
{{ stats.plats }}
</div>

</div>


<div class="card">

<h3>Tables occupées</h3>

<div class="number">
{{ stats.tables }}
</div>

</div>

</div>


<!-- COMMANDES RECENTES -->

<div class="section">

<h2>Dernières commandes</h2>

<table>

<tr>

<th>ID</th>
<th>Client</th>
<th>Table</th>
<th>Total</th>
<th>Statut</th>
<th>Date</th>

</tr>

{% for c in commandes %}

<tr>

<td>#{{ c.id }}</td>

<td>{{ c.client }}</td>

<td>
{% if c.numero %}
Table {{ c.numero }}
{% else %}
-
{% endif %}
</td>

<td>
{{ "%.0f"|format(c.total) }} FCFA
</td>

<td>

<span class="badge">
{{ c.statut }}
</span>

</td>

<td>{{ c.date_commande }}</td>

</tr>

{% else %}

<tr>

<td colspan="6">
Aucune commande.
</td>

</tr>

{% endfor %}

</table>

</div>


<!-- NOUVELLE COMMANDE -->

<div class="section">

<h2>Créer une commande</h2>

<form method="POST"
action="/commandes/ajouter">

<input
name="client"
placeholder="Nom du client"
required
>

<select name="table_id">

<option value="">
Sans table
</option>

{% for t in tables %}

{% if t.statut == "Libre" %}

<option value="{{ t.id }}">

Table {{ t.numero }}

</option>

{% endif %}

{% endfor %}

</select>

<button class="btn"
type="submit">

Créer la commande

</button>

</form>

</div>

</div>

</body>

</html>

"""


# =========================
# DASHBOARD
# =========================

@app.route("/")
def index():

    with get_db() as db:

        stats = {

            "commandes":
                db.execute(
                    "SELECT COUNT(*) FROM commandes"
                ).fetchone()[0],

            "ca":
                db.execute("""
                    SELECT COALESCE(SUM(total),0)
                    FROM commandes
                    WHERE statut = 'Payée'
                """).fetchone()[0],

            "plats":
                db.execute(
                    "SELECT COUNT(*) FROM plats"
                ).fetchone()[0],

            "tables":
                db.execute("""
                    SELECT COUNT(*)
                    FROM tables_restaurant
                    WHERE statut = 'Occupée'
                """).fetchone()[0]
        }

        commandes = db.execute("""
            SELECT
                c.*,
                t.numero
            FROM commandes c
            LEFT JOIN tables_restaurant t
                ON c.table_id = t.id
            ORDER BY c.id DESC
            LIMIT 10
        """).fetchall()

        tables = db.execute("""
            SELECT *
            FROM tables_restaurant
            ORDER BY numero
        """).fetchall()

    return render_template_string(
        PAGE,
        stats=stats,
        commandes=commandes,
        tables=tables
    )


# =========================
# PLATS
# =========================

@app.route("/plats")
def plats():

    with get_db() as db:

        plats = db.execute("""
            SELECT *
            FROM plats
            ORDER BY categorie, nom
        """).fetchall()

    page = """

    <h1>🍽️ Gestion des plats</h1>

    <a href="/">← Dashboard</a>

    <h2>Ajouter un plat</h2>

    <form method="POST"
    action="/plats/ajouter">

    <input
    name="nom"
    placeholder="Nom du plat"
    required
    >

    <input
    name="categorie"
    placeholder="Catégorie"
    required
    >

    <input
    name="prix"
    type="number"
    placeholder="Prix FCFA"
    required
    >

    <button>Ajouter</button>

    </form>

    <h2>Menu</h2>

    <table border="1" cellpadding="10">

    <tr>
    <th>ID</th>
    <th>Nom</th>
    <th>Catégorie</th>
    <th>Prix</th>
    <th>Disponibilité</th>
    <th>Action</th>
    </tr>

    {% for p in plats %}

    <tr>

    <td>{{ p.id }}</td>

    <td>{{ p.nom }}</td>

    <td>{{ p.categorie }}</td>

    <td>{{ "%.0f"|format(p.prix) }} FCFA</td>

    <td>

    {% if p.disponible %}
    Disponible
    {% else %}
    Indisponible
    {% endif %}

    </td>

    <td>

    <form method="POST"
    action="/plats/supprimer/{{ p.id }}">

    <button>
    Supprimer
    </button>

    </form>

    </td>

    </tr>

    {% endfor %}

    </table>

    """

    return render_template_string(
        page,
        plats=plats
    )


@app.route("/plats/ajouter", methods=["POST"])
def ajouter_plat():

    nom = request.form["nom"]
    categorie = request.form["categorie"]
    prix = float(request.form["prix"])

    with get_db() as db:

        db.execute("""
            INSERT INTO plats
            (nom, categorie, prix)
            VALUES (?, ?, ?)
        """, (nom, categorie, prix))

    update_metrics()

    return redirect(url_for("plats"))


@app.route("/plats/supprimer/<int:pid>",
           methods=["POST"])
def supprimer_plat(pid):

    with get_db() as db:

        db.execute(
            "DELETE FROM plats WHERE id = ?",
            (pid,)
        )

    update_metrics()

    return redirect(url_for("plats"))


# =========================
# TABLES
# =========================

@app.route("/tables")
def tables():

    with get_db() as db:

        tables = db.execute("""
            SELECT *
            FROM tables_restaurant
            ORDER BY numero
        """).fetchall()

    page = """

    <h1>🪑 Gestion des tables</h1>

    <a href="/">← Dashboard</a>

    <table border="1" cellpadding="10">

    <tr>
    <th>Table</th>
    <th>Capacité</th>
    <th>Statut</th>
    <th>Action</th>
    </tr>

    {% for t in tables %}

    <tr>

    <td>Table {{ t.numero }}</td>

    <td>{{ t.capacite }} personnes</td>

    <td>{{ t.statut }}</td>

    <td>

    {% if t.statut == "Libre" %}

    <form method="POST"
    action="/tables/{{ t.id }}/occuper">

    <button>
    Occuper
    </button>

    </form>

    {% else %}

    <form method="POST"
    action="/tables/{{ t.id }}/liberer">

    <button>
    Libérer
    </button>

    </form>

    {% endif %}

    </td>

    </tr>

    {% endfor %}

    </table>

    """

    return render_template_string(
        page,
        tables=tables
    )


@app.route("/tables/<int:tid>/occuper",
           methods=["POST"])
def occuper_table(tid):

    with get_db() as db:

        db.execute("""
            UPDATE tables_restaurant
            SET statut = 'Occupée'
            WHERE id = ?
        """, (tid,))

    update_metrics()

    return redirect(url_for("tables"))


@app.route("/tables/<int:tid>/liberer",
           methods=["POST"])
def liberer_table(tid):

    with get_db() as db:

        db.execute("""
            UPDATE tables_restaurant
            SET statut = 'Libre'
            WHERE id = ?
        """, (tid,))

    update_metrics()

    return redirect(url_for("tables"))


# =========================
# COMMANDES
# =========================

@app.route("/commandes")
def commandes():

    with get_db() as db:

        commandes = db.execute("""
            SELECT
                c.*,
                t.numero
            FROM commandes c
            LEFT JOIN tables_restaurant t
                ON c.table_id = t.id
            ORDER BY c.id DESC
        """).fetchall()

    page = """

    <h1>🧾 Gestion des commandes</h1>

    <a href="/">← Dashboard</a>

    <table border="1" cellpadding="10">

    <tr>

    <th>ID</th>
    <th>Client</th>
    <th>Table</th>
    <th>Total</th>
    <th>Statut</th>
    <th>Actions</th>

    </tr>

    {% for c in commandes %}

    <tr>

    <td>#{{ c.id }}</td>

    <td>{{ c.client }}</td>

    <td>
    {% if c.numero %}
    Table {{ c.numero }}
    {% else %}
    -
    {% endif %}
    </td>

    <td>
    {{ "%.0f"|format(c.total) }} FCFA
    </td>

    <td>{{ c.statut }}</td>

    <td>

    {% if c.statut == "En attente" %}

    <form method="POST"
    action="/commandes/{{ c.id }}/preparation">

    <button>
    Préparer
    </button>

    </form>

    {% elif c.statut == "Préparation" %}

    <form method="POST"
    action="/commandes/{{ c.id }}/servie">

    <button>
    Servie
    </button>

    </form>

    {% elif c.statut == "Servie" %}

    <form method="POST"
    action="/commandes/{{ c.id }}/payer">

    <button>
    Payer
    </button>

    </form>

    {% endif %}

    </td>

    </tr>

    {% endfor %}

    </table>

    """

    return render_template_string(
        page,
        commandes=commandes
    )


@app.route("/commandes/ajouter",
           methods=["POST"])
def ajouter_commande():

    client = request.form["client"]

    table_id = request.form.get("table_id")

    with get_db() as db:

        cursor = db.execute("""
            INSERT INTO commandes
            (client, table_id, total, statut)
            VALUES (?, ?, ?, ?)
        """, (
            client,
            table_id if table_id else None,
            0,
            "En attente"
        ))

        commande_id = cursor.lastrowid

        if table_id:

            db.execute("""
                UPDATE tables_restaurant
                SET statut = 'Occupée'
                WHERE id = ?
            """, (table_id,))

    update_metrics()

    return redirect(url_for("commandes"))


# =========================
# CHANGEMENT STATUT COMMANDE
# =========================

@app.route("/commandes/<int:cid>/preparation",
           methods=["POST"])
def commande_preparation(cid):

    with get_db() as db:

        db.execute("""
            UPDATE commandes
            SET statut = 'Préparation'
            WHERE id = ?
        """, (cid,))

    update_metrics()

    return redirect(url_for("commandes"))


@app.route("/commandes/<int:cid>/servie",
           methods=["POST"])
def commande_servie(cid):

    with get_db() as db:

        db.execute("""
            UPDATE commandes
            SET statut = 'Servie'
            WHERE id = ?
        """, (cid,))

    update_metrics()

    return redirect(url_for("commandes"))


@app.route("/commandes/<int:cid>/payer",
           methods=["POST"])
def commande_payer(cid):

    with get_db() as db:

        commande = db.execute("""
            SELECT table_id
            FROM commandes
            WHERE id = ?
        """, (cid,)).fetchone()

        db.execute("""
            UPDATE commandes
            SET statut = 'Payée'
            WHERE id = ?
        """, (cid,))

        if commande and commande["table_id"]:

            db.execute("""
                UPDATE tables_restaurant
                SET statut = 'Libre'
                WHERE id = ?
            """, (commande["table_id"],))

    update_metrics()

    return redirect(url_for("commandes"))


# =========================
# HEALTH CHECK
# =========================

@app.route("/health")
def health():

    return {
        "status": "ok",
        "application": "Dabstyle Restaurant"
    }


# =========================
# INITIALISATION
# =========================

init_db()
update_metrics()


if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
    )