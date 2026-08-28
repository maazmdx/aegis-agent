#!/usr/bin/env python
"""dashboard/main.py - Live Aegis fleet dashboard served by Flask.

Reads incident data from Firestore in real time and renders a Material
Design 3 dark-theme UI.  The ``/incident/<id>`` route returns JSON so the
"Review" modal can fetch live triage details without a full page reload.

Routes
------
GET /           – Main dashboard page (auto-refreshes every 5 s via JS).
GET /incident/<id> – JSON detail for one incident (used by the Review modal).
GET /healthz    – Health-check endpoint.

Environment variables
---------------------
PROJECT_ID – GCP project (default: aegis-hackathon-506413).
PORT       – TCP port to bind (default: 8080).
"""

import base64
import json
import logging
import os
import time
import uuid
from collections import defaultdict

import requests as http_requests
from flask import Flask, jsonify, render_template_string, request
from google.cloud import firestore
from google import genai

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "aegis-hackathon-506413")
COLLECTION = "incidents"

_db = None

def _get_db():
    global _db
    if _db is None:
        _db = firestore.Client(project=PROJECT_ID)
    return _db

ACTIVE_STATUSES = {"open", "diagnosed", "decided", "remediating"}
AGENTS = ["invoice-agent", "support-agent", "research-agent"]

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Pure helper (no Firestore dependency — easily unit-tested)
# ---------------------------------------------------------------------------

def compute_mttd(deltas: list[float]) -> float | str:
    """Compute mean time to diagnose from a list of valid deltas in seconds.

    Args:
        deltas: List of positive float values, each < 3 600 s.

    Returns:
        Rounded average (1 decimal) or the em-dash string ``"—"`` when empty.
    """
    if not deltas:
        return "\u2014"
    return round(sum(deltas) / len(deltas), 1)


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Aegis Fleet Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@24,400,0,0" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg-dark:   #121316;
      --bg-panel:  #1A1C20;
      --bg-card:   #222429;
      --text-main: #FFFFFF;
      --text-muted:#9BA1A6;
      --google-blue:  #4285F4;
      --google-green: #34A853;
      --google-yellow:#FBBC04;
      --google-red:   #EA4335;
      --border: rgba(255,255,255,0.08);
      --font: 'Inter', sans-serif;
    }
    *{box-sizing:border-box;margin:0;padding:0;}
    body{font-family:var(--font);background:var(--bg-dark);color:var(--text-main);display:flex;min-height:100vh;overflow-x:hidden;}

    /* ── Sidebar ── */
    aside{width:240px;background:var(--bg-panel);border-right:1px solid var(--border);display:flex;flex-direction:column;padding:24px 16px;flex-shrink:0;}
    .brand{font-size:1.4rem;font-weight:700;margin-bottom:28px;padding-left:8px;display:flex;align-items:center;gap:10px;}
    .brand-dot{width:9px;height:9px;background:var(--google-green);border-radius:50%;}
    .brand-letters span:nth-child(1){color:var(--google-blue);}
    .brand-letters span:nth-child(2){color:var(--google-red);}
    .brand-letters span:nth-child(3){color:var(--google-yellow);}
    .brand-letters span:nth-child(4){color:var(--google-blue);}
    .brand-letters span:nth-child(5){color:var(--google-green);}
    .btn-primary{background:var(--google-blue);color:#fff;border:none;border-radius:12px;padding:12px 16px;font-size:.9rem;font-weight:600;font-family:var(--font);display:flex;align-items:center;gap:8px;cursor:pointer;margin-bottom:28px;transition:background .2s;width:100%;}
    .btn-primary:hover{background:#3b77db;}
    .nav-menu{list-style:none;display:flex;flex-direction:column;gap:2px;flex:1;}
    .nav-item{display:flex;align-items:center;gap:12px;padding:11px 14px;border-radius:8px;color:var(--text-muted);text-decoration:none;font-size:.9rem;font-weight:500;transition:all .15s;cursor:pointer;}
    .nav-item:hover{background:rgba(255,255,255,.06);color:var(--text-main);}
    .nav-item.active{background:rgba(255,255,255,.1);color:var(--text-main);}
    .nav-item.logout{margin-top:auto;color:var(--google-red);}
    .nav-item.logout:hover{background:rgba(234,67,53,.1);}

    /* ── Main ── */
    main{flex:1;display:flex;flex-direction:column;min-width:0;}
    header{height:68px;display:flex;justify-content:flex-end;align-items:center;padding:0 32px;border-bottom:1px solid var(--border);gap:16px;}
    .top-pill{background:var(--bg-card);border:1px solid var(--border);padding:6px 14px;border-radius:20px;font-size:.8rem;color:var(--text-muted);display:flex;align-items:center;gap:6px;}
    .icon-btn{color:var(--text-muted);display:flex;align-items:center;cursor:pointer;}

    /* ── Content ── */
    .content{padding:36px;overflow-y:auto;position:relative;}
    .glow{position:absolute;top:0;left:10%;width:600px;height:180px;background:radial-gradient(ellipse at top,rgba(66,133,244,.12),transparent 70%);pointer-events:none;}
    h1{font-size:2.25rem;font-weight:600;margin-bottom:6px;letter-spacing:-.5px;}
    .subtitle{color:var(--text-muted);font-size:.95rem;margin-bottom:36px;}

    /* ── Metric cards ── */
    .metrics-row{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;margin-bottom:36px;}
    .metric-card{background:var(--bg-panel);border:1px solid var(--border);border-radius:16px;padding:22px;display:flex;flex-direction:column;gap:12px;transition:transform .2s,box-shadow .2s;cursor:default;}
    .metric-card:hover{transform:translateY(-3px);box-shadow:0 8px 24px rgba(0,0,0,.25);}
    .metric-header{display:flex;justify-content:space-between;align-items:center;}
    .metric-title{display:flex;align-items:center;gap:8px;color:var(--text-muted);font-size:.85rem;font-weight:500;}
    .trend-pill{font-size:.7rem;font-weight:700;padding:2px 8px;border-radius:12px;}
    .trend-red{background:rgba(234,67,53,.15);color:var(--google-red);}
    .trend-green{background:rgba(52,168,83,.15);color:var(--google-green);}
    .trend-yellow{background:rgba(251,188,4,.15);color:var(--google-yellow);}
    .metric-value{font-size:2rem;font-weight:600;letter-spacing:-1px;}

    /* ── Fleet section ── */
    .fleet-section{margin-bottom:36px;}
    .fleet-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;}
    .fleet-card{background:var(--bg-panel);border:1px solid var(--border);border-radius:16px;padding:20px;display:flex;align-items:center;gap:16px;transition:transform .2s,box-shadow .2s;}
    .fleet-card:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,.2);}
    .fleet-icon{width:40px;height:40px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:20px;}
    .fleet-icon.healthy{background:rgba(52,168,83,.15);color:var(--google-green);}
    .fleet-icon.active{background:rgba(234,67,53,.15);color:var(--google-red);}
    .fleet-info{flex:1;}
    .fleet-name{font-weight:600;font-size:.95rem;margin-bottom:2px;}
    .fleet-stats{font-size:.8rem;color:var(--text-muted);}
    .fleet-badge{font-size:.7rem;font-weight:600;padding:3px 10px;border-radius:12px;}
    .fleet-badge.healthy{background:rgba(52,168,83,.15);color:var(--google-green);}
    .fleet-badge.active{background:rgba(234,67,53,.15);color:var(--google-red);}

    /* ── Bottom row ── */
    .bottom-row{display:flex;gap:20px;flex-wrap:wrap;}
    .table-section{flex:2;min-width:340px;background:var(--bg-panel);border:1px solid var(--border);border-radius:16px;padding:24px;}
    .chart-section{flex:1;min-width:260px;background:var(--bg-panel);border:1px solid var(--border);border-radius:16px;padding:24px;display:flex;flex-direction:column;}
    .section-title{font-size:1.1rem;font-weight:600;margin-bottom:20px;}

    /* ── Table ── */
    table{width:100%;border-collapse:collapse;}
    th{text-align:left;color:var(--text-muted);font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:.04em;padding-bottom:14px;border-bottom:1px solid var(--border);}
    td{padding:14px 0;border-bottom:1px solid rgba(255,255,255,.03);font-size:.875rem;}
    tr:last-child td{border-bottom:none;}
    .agent-cell{display:flex;align-items:center;gap:8px;font-weight:500;}
    .agent-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;}

    /* ── Pills ── */
    .pill{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:6px;font-size:.72rem;font-weight:600;border:1px solid;}
    .pill-yellow{border-color:rgba(251,188,4,.4);color:var(--google-yellow);background:rgba(251,188,4,.07);}
    .pill-red{border-color:rgba(234,67,53,.4);color:var(--google-red);background:rgba(234,67,53,.07);}
    .pill-green{border-color:rgba(52,168,83,.4);color:var(--google-green);background:rgba(52,168,83,.07);}
    .pill-grey{border-color:rgba(255,255,255,.15);color:var(--text-muted);background:rgba(255,255,255,.04);}

    /* ── Review button ── */
    .btn-review{background:none;border:none;color:var(--google-blue);font-weight:600;font-size:.85rem;font-family:var(--font);cursor:pointer;padding:4px 0;transition:opacity .15s;}
    .btn-review:hover{opacity:.75;}
    .btn-view{color:var(--text-main);font-weight:500;font-size:.85rem;font-family:var(--font);background:none;border:none;cursor:pointer;padding:4px 0;transition:opacity .15s;}
    .btn-view:hover{opacity:.75;}

    /* ── Chart ── */
    .chart-container{position:relative;flex:1;display:flex;align-items:center;justify-content:center;min-height:220px;}
    .chart-center{position:absolute;text-align:center;pointer-events:none;}
    .chart-center-val{font-size:2.25rem;font-weight:600;letter-spacing:-1px;}
    .chart-center-label{font-size:.75rem;color:var(--text-muted);}
    .chart-legend{margin-top:20px;display:flex;flex-direction:column;gap:10px;}
    .legend-item{display:flex;align-items:center;gap:10px;font-size:.82rem;}
    .legend-dot{width:9px;height:9px;border-radius:50%;}

    /* ── Modal overlay ── */
    .modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:200;align-items:center;justify-content:center;}
    .modal-overlay.open{display:flex;}
    .modal{background:var(--bg-panel);border:1px solid var(--border);border-radius:20px;padding:32px;max-width:680px;width:90%;max-height:85vh;overflow-y:auto;position:relative;}
    .modal-close{position:absolute;top:16px;right:16px;background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:1.4rem;}
    .modal-close:hover{color:var(--text-main);}
    .modal-title{font-size:1.2rem;font-weight:700;margin-bottom:4px;}
    .modal-subtitle{font-size:.85rem;color:var(--text-muted);margin-bottom:24px;}
    .timeline{display:flex;flex-direction:column;gap:0;}
    .tl-item{display:flex;gap:16px;padding-bottom:20px;position:relative;}
    .tl-item:not(:last-child)::before{content:'';position:absolute;left:11px;top:24px;width:2px;bottom:0;background:var(--border);}
    .tl-icon{width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:14px;margin-top:1px;}
    .tl-icon.blue{background:rgba(66,133,244,.2);color:var(--google-blue);}
    .tl-icon.green{background:rgba(52,168,83,.2);color:var(--google-green);}
    .tl-icon.yellow{background:rgba(251,188,4,.2);color:var(--google-yellow);}
    .tl-icon.red{background:rgba(234,67,53,.2);color:var(--google-red);}
    .tl-icon.grey{background:rgba(155,161,166,.15);color:var(--text-muted);}
    .tl-body{}
    .tl-label{font-size:.75rem;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted);margin-bottom:4px;}
    .tl-content{font-size:.88rem;line-height:1.6;color:var(--text-main);}
    .tl-content strong{font-weight:600;}
    .kv{display:grid;grid-template-columns:max-content 1fr;gap:6px 16px;font-size:.85rem;margin-bottom:16px;}
    .kv dt{color:var(--text-muted);font-weight:500;}
    .kv dd{color:var(--text-main);}
    .modal-spinner{text-align:center;padding:40px;color:var(--text-muted);}
    .modal-error{color:var(--google-red);padding:16px;text-align:center;}

    /* ── Settings panel ── */
    .settings-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:200;align-items:center;justify-content:center;}
    .settings-overlay.open{display:flex;}
    .settings-panel{background:var(--bg-panel);border:1px solid var(--border);border-radius:20px;padding:32px;max-width:480px;width:90%;position:relative;}
    .settings-panel h2{font-size:1.1rem;font-weight:700;margin-bottom:20px;}
    .setting-row{display:flex;justify-content:space-between;align-items:center;padding:12px 0;border-bottom:1px solid var(--border);}
    .setting-row:last-child{border-bottom:none;}
    .setting-label{font-size:.9rem;color:var(--text-main);}
    .setting-value{font-size:.85rem;color:var(--text-muted);font-family:monospace;}
    .settings-close{position:absolute;top:16px;right:16px;background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:1.4rem;}
    .settings-close:hover{color:var(--text-main);}

    /* ── Chatbot ── */
    #chat-widget { position: fixed; bottom: 24px; right: 24px; width: 380px; max-height: 600px; background: var(--bg-panel); border: 1px solid var(--border); border-radius: 16px; display: flex; flex-direction: column; z-index: 100; box-shadow: 0 10px 30px rgba(0,0,0,0.5); transform: translateY(120%); transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275); }
    #chat-widget.open { transform: translateY(0); }
    #chat-header { padding: 16px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: rgba(255,255,255,0.02); border-radius: 16px 16px 0 0; }
    #chat-header-title { font-weight: 600; display: flex; align-items: center; gap: 8px; }
    #chat-close { background: none; border: none; color: var(--text-muted); cursor: pointer; }
    #chat-messages { padding: 16px; flex-grow: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 12px; min-height: 300px; max-height: 400px; }
    .msg { padding: 10px 14px; border-radius: 12px; font-size: 0.9rem; line-height: 1.4; max-width: 85%; }
    .msg.user { background: rgba(66,133,244,0.15); color: var(--google-blue); align-self: flex-end; border-bottom-right-radius: 4px; }
    .msg.bot { background: rgba(255,255,255,0.05); color: var(--text-main); align-self: flex-start; border-bottom-left-radius: 4px; }
    .msg.bot p { margin: 0 0 8px 0; }
    .msg.bot p:last-child { margin-bottom: 0; }
    .msg.bot ul { margin: 0; padding-left: 20px; }
    .msg.bot li { margin-bottom: 4px; }
    .msg.bot strong { color: #fff; }
    #chat-input-container { padding: 12px; border-top: 1px solid var(--border); display: flex; gap: 8px; }
    #chat-input { flex-grow: 1; background: var(--bg-body); border: 1px solid var(--border); border-radius: 20px; padding: 10px 16px; color: var(--text-main); outline: none; }
    #chat-input:focus { border-color: rgba(255,255,255,0.2); }
    #chat-send { background: var(--google-blue); color: #fff; border: none; border-radius: 50%; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; cursor: pointer; }
    #chat-toggle-btn { position: fixed; bottom: 24px; right: 24px; background: var(--google-blue); color: #fff; border: none; border-radius: 50%; width: 56px; height: 56px; display: flex; align-items: center; justify-content: center; cursor: pointer; box-shadow: 0 4px 12px rgba(0,0,0,0.3); z-index: 99; transition: transform 0.2s; }
    #chat-toggle-btn:hover { transform: scale(1.05); }
  </style>
</head>
<body>

<aside>
  <div class="brand">
    <div class="brand-dot"></div>
    <div class="brand-letters">
      <span>A</span><span>e</span><span>g</span><span>i</span><span>s</span>
    </div>
  </div>

  <button class="btn-primary" onclick="window.location.href='/'">
    <span class="material-symbols-outlined" style="font-size:18px">refresh</span>
    Refresh Now
  </button>

  <nav class="nav-menu">
    <a href="/" class="nav-item active" id="nav-dashboard">
      <span class="material-symbols-outlined">grid_view</span> Dashboard
    </a>
    <a href="#fleet" class="nav-item" id="nav-fleet" onclick="scrollToSection('fleet-section', event)">
      <span class="material-symbols-outlined">smart_toy</span> Fleet
    </a>
    <a href="#incidents" class="nav-item" id="nav-incidents" onclick="scrollToSection('incidents', event)">
      <span class="material-symbols-outlined">warning</span> Incidents
    </a>
    <a href="/healthz" target="_blank" class="nav-item" id="nav-health">
      <span class="material-symbols-outlined">health_and_safety</span> Health
    </a>
    <a href="#" class="nav-item" id="nav-settings" onclick="openSettings(event)">
      <span class="material-symbols-outlined">settings</span> Settings
    </a>
    <a href="#" class="nav-item logout" id="nav-logout" onclick="confirmLogout(event)">
      <span class="material-symbols-outlined">logout</span> Log Out
    </a>
  </nav>
</aside>

<main>
  <header>
    <div class="top-pill">
      <span class="material-symbols-outlined" style="font-size:14px">autorenew</span>
      auto-refresh 5s
    </div>
    <button class="btn-primary" style="margin:0; width:auto; padding:8px 16px; background:var(--google-red);" onclick="triggerIncident()">
      <span class="material-symbols-outlined" style="font-size:18px">warning</span>
      Simulate Incident
    </button>
  </header>

  <div class="content">
    <div class="glow"></div>
    <h1>Overview</h1>
    <div class="subtitle">Real-time fleet monitoring and incident resolution.</div>

    <!-- Metrics -->
    <div class="metrics-row">
      <div class="metric-card">
        <div class="metric-header">
          <div class="metric-title">
            <span class="material-symbols-outlined" style="color:var(--google-red);font-size:18px">warning</span>
            Incidents Caught
          </div>
          <div class="trend-pill trend-red">live</div>
        </div>
        <div class="metric-value">{{ metrics.total_incidents }}</div>
      </div>

      <div class="metric-card">
        <div class="metric-header">
          <div class="metric-title">
            <span class="material-symbols-outlined" style="color:var(--google-green);font-size:18px">check_circle</span>
            Auto-Resolved %
          </div>
          <div class="trend-pill trend-green">live</div>
        </div>
        <div class="metric-value">{{ metrics.auto_resolved_pct }}%</div>
      </div>

      <div class="metric-card">
        <div class="metric-header">
          <div class="metric-title">
            <span class="material-symbols-outlined" style="color:var(--google-yellow);font-size:18px">timer</span>
            Mean Time to Diagnose
          </div>
          <div class="trend-pill trend-yellow">avg</div>
        </div>
        <div class="metric-value">{{ metrics.mttd }}{{ 's' if metrics.mttd != '—' else '' }}</div>
      </div>

      <div class="metric-card">
        <div class="metric-header">
          <div class="metric-title">
            <span class="material-symbols-outlined" style="color:var(--google-blue);font-size:18px">monetization_on</span>
            Cost / Tokens Flagged
          </div>
        </div>
        <div class="metric-value">${{ "%.2f"|format(metrics.cost_flagged) }}</div>
      </div>
    </div>

    <!-- Fleet section -->
    <div class="fleet-section" id="fleet-section">
      <div class="section-title">Agent Fleet</div>
      <div class="fleet-grid">
        {% for agent in agents %}
        {% set status = 'active' if agent.active_count > 0 else 'healthy' %}
        <div class="fleet-card">
          <div class="fleet-icon {{ status }}">
            <span class="material-symbols-outlined">smart_toy</span>
          </div>
          <div class="fleet-info">
            <div class="fleet-name">{{ agent.name }}</div>
            <div class="fleet-stats">Active: {{ agent.active_count }} · Total: {{ agent.total_count }}</div>
          </div>
          <div class="fleet-badge {{ status }}">{{ 'INCIDENTS' if status == 'active' else 'HEALTHY' }}</div>
        </div>
        {% endfor %}
      </div>
    </div>

    <!-- Bottom row: table + chart -->
    <div class="bottom-row">
      <div class="table-section" id="incidents">
        <div class="section-title">Recent Incidents</div>
        {% if incidents %}
        <table>
          <thead>
            <tr>
              <th>Agent</th><th>Type</th><th>Severity</th><th>Status</th><th>Action</th>
            </tr>
          </thead>
          <tbody>
            {% for inc in incidents %}
            {% set sev_cls  = 'pill-red'    if inc.severity == 'high'       else ('pill-yellow' if inc.severity == 'medium' else 'pill-grey') %}
            {% set stat_cls = 'pill-green'  if inc.status  == 'resolved'    else ('pill-red' if inc.status in ['escalated','open'] else 'pill-yellow') %}
            {% set dot_col  = '#EA4335' if inc.agent == 'invoice-agent' else ('#34A853' if inc.agent == 'support-agent' else '#FBBC04') %}
            <tr>
              <td>
                <div class="agent-cell">
                  <div class="agent-dot" style="background:{{ dot_col }}"></div>
                  {{ inc.agent }}
                </div>
              </td>
              <td style="color:var(--text-muted)">{{ inc.type | replace('_',' ') | title }}</td>
              <td><span class="pill {{ sev_cls }}">{{ inc.severity | title }}</span></td>
              <td><span class="pill {{ stat_cls }}">{{ inc.status | title }}</span></td>
              <td>
                {% if inc.status == 'resolved' %}
                  <button class="btn-view" onclick="openModal('{{ inc.incident_id }}')">View Log</button>
                {% else %}
                  <button class="btn-review" onclick="openModal('{{ inc.incident_id }}')">Review</button>
                {% endif %}
              </td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
        {% else %}
        <div style="padding:40px;text-align:center;color:var(--text-muted)">No incidents recorded yet.</div>
        {% endif %}
      </div>

      <div class="chart-section">
        <div class="section-title">Resolution Rate</div>
        <div class="chart-container">
          <canvas id="resolutionChart"></canvas>
          <div class="chart-center">
            <div class="chart-center-val">{{ metrics.auto_resolved_pct }}%</div>
            <div class="chart-center-label">Auto</div>
          </div>
        </div>
        <div class="chart-legend">
          <div class="legend-item"><div class="legend-dot" style="background:var(--google-green)"></div>Auto-Resolved</div>
          <div class="legend-item"><div class="legend-dot" style="background:var(--google-blue)"></div>Manual Intervention</div>
          <div class="legend-item"><div class="legend-dot" style="background:var(--google-red)"></div>Open / Escalated</div>
        </div>
      </div>
    </div>
  </div>
</main>

<!-- ── Review / Detail Modal ── -->
<div class="modal-overlay" id="modal" onclick="maybeCloseModal(event)">
  <div class="modal" id="modal-box">
    <button class="modal-close" onclick="closeModal()">
      <span class="material-symbols-outlined">close</span>
    </button>
    <div id="modal-body"><div class="modal-spinner">Loading…</div></div>
  </div>
</div>

<!-- ── Settings Panel ── -->
<div class="settings-overlay" id="settings-overlay" onclick="maybeCloseSettings(event)">
  <div class="settings-panel" id="settings-panel">
    <button class="settings-close" onclick="closeSettings()">
      <span class="material-symbols-outlined">close</span>
    </button>
    <h2><span class="material-symbols-outlined" style="font-size:20px;vertical-align:middle;margin-right:8px;">settings</span>Dashboard Settings</h2>
    <div class="setting-row">
      <div class="setting-label">Auto-refresh interval</div>
      <div class="setting-value">5 seconds</div>
    </div>
    <div class="setting-row">
      <div class="setting-label">GCP Project</div>
      <div class="setting-value">{{ project_id }}</div>
    </div>
    <div class="setting-row">
      <div class="setting-label">Firestore collection</div>
      <div class="setting-value">incidents</div>
    </div>
    <div class="setting-row">
      <div class="setting-label">Agents monitored</div>
      <div class="setting-value">3</div>
    </div>
    <div class="setting-row">
      <div class="setting-label">Dashboard version</div>
      <div class="setting-value">1.0.0</div>
    </div>
  </div>
</div>

<!-- Chatbot UI -->
<button id="chat-toggle-btn" onclick="toggleChat()">
  <span class="material-symbols-outlined">smart_toy</span>
</button>
<div id="chat-widget">
  <div id="chat-header">
    <div id="chat-header-title">
      <span class="material-symbols-outlined" style="font-size: 18px; color: var(--google-blue);">smart_toy</span>
      Reporting Agent
    </div>
    <button id="chat-close" onclick="toggleChat()"><span class="material-symbols-outlined">close</span></button>
  </div>
  <div id="chat-messages">
    <div class="msg bot">Hi! I can compile incident metrics and summarize current alerts for you. What do you need?</div>
  </div>
  <div id="chat-input-container">
    <input type="text" id="chat-input" placeholder="Ask about incidents..." onkeypress="handleChatEnter(event)">
    <button id="chat-send" onclick="sendChatMessage()"><span class="material-symbols-outlined" style="font-size: 18px;">send</span></button>
  </div>
</div>

<script>
  // ── Auto-refresh ──────────────────────────────────────────────────────────
  setTimeout(() => {
    const isSettingsOpen = document.getElementById('settings-overlay')?.classList.contains('open');
    const isModalOpen = document.getElementById('modal')?.classList.contains('open');
    const isChatOpen = document.getElementById('chat-widget')?.classList.contains('open');
    
    if (!isSettingsOpen && !isModalOpen && !isChatOpen) {
      window.location.reload();
    } else {
      // If modal or chat is open, wait and try again later
      setInterval(() => {
        const checkSettings = document.getElementById('settings-overlay')?.classList.contains('open');
        const checkModal = document.getElementById('modal')?.classList.contains('open');
        const checkChat = document.getElementById('chat-widget')?.classList.contains('open');
        if (!checkSettings && !checkModal && !checkChat) window.location.reload();
      }, 5000);
    }
  }, 5000);

  // ── Sidebar navigation ────────────────────────────────────────────────────
  function scrollToSection(id, event) {
    if (event) {
      event.preventDefault();
      document.querySelectorAll('.nav-item').forEach(nav => nav.classList.remove('active'));
      event.currentTarget.classList.add('active');
    }
    var el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({behavior: 'smooth', block: 'start'});
    }
  }

  function openSettings(e) {
    e.preventDefault();
    document.getElementById('settings-overlay').classList.add('open');
  }

  function closeSettings() {
    document.getElementById('settings-overlay').classList.remove('open');
  }

  function maybeCloseSettings(e) {
    if (e.target === document.getElementById('settings-overlay')) closeSettings();
  }

  function confirmLogout(e) {
    e.preventDefault();
    if (confirm('Log out of Aegis dashboard?')) {
      alert('Logout is not available in demo mode.');
    }
  }

  // ── Chart.js ─────────────────────────────────────────────────────────────
  Chart.defaults.animation = false;
  const pct       = {{ metrics.auto_resolved_pct }};
  const remainder = 100 - pct;
  const manual    = Math.floor(remainder * 0.6);
  const failed    = remainder - manual;

  new Chart(document.getElementById('resolutionChart').getContext('2d'), {
    type: 'doughnut',
    data: {
      labels: ['Auto-Resolved', 'Manual', 'Open/Escalated'],
      datasets: [{
        data: [pct || 1, manual, failed],
        backgroundColor: ['#34A853', '#4285F4', '#EA4335'],
        borderWidth: 4,
        borderColor: '#1A1C20',
      }]
    },
    options: {
      cutout: '78%',
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#222429',
          titleColor: '#fff',
          bodyColor: '#fff',
          borderColor: 'rgba(255,255,255,.08)',
          borderWidth: 1,
        }
      }
    }
  });

  // ── Modal helpers ─────────────────────────────────────────────────────────
  function openModal(incidentId) {
    document.getElementById('modal-body').innerHTML = '<div class="modal-spinner">Loading triage details…</div>';
    document.getElementById('modal').classList.add('open');

    fetch('/incident/' + incidentId)
      .then(r => r.ok ? r.json() : Promise.reject(r.status))
      .then(renderModal)
      .catch(err => {
        document.getElementById('modal-body').innerHTML =
          '<div class="modal-error">Failed to load incident details (error ' + err + ').</div>';
      });
  }

  function closeModal() {
    document.getElementById('modal').classList.remove('open');
  }

  function maybeCloseModal(event) {
    if (event.target === document.getElementById('modal')) closeModal();
  }

  function pill(text, cls) {
    return '<span class="pill ' + cls + '">' + text + '</span>';
  }

  function sevCls(sev) {
    return sev === 'high' ? 'pill-red' : (sev === 'medium' ? 'pill-yellow' : 'pill-grey');
  }

  function statCls(s) {
    return s === 'resolved' ? 'pill-green' : (s === 'open' || s === 'escalated' ? 'pill-red' : 'pill-yellow');
  }

  function renderModal(inc) {
    const diag   = inc.diagnosis   || {};
    const dec    = inc.decision    || {};
    const rem    = inc.remediation || {};
    const pm     = (inc.postmortem || {}).summary || '';
    const audit  = inc.audit_log   || [];
    const status = inc.status || 'open';

    let steps = '';

    // ── Step 1: Detection ──────────────────────────────────────────────────
    const detectedTs = inc.created_at && inc.created_at !== "None" ? new Date(inc.created_at).toLocaleTimeString() : '';
    steps += tlItem('blue', 'sensors', 'Detected by Aegis Detector', `
      <dl class="kv">
        <dt>Agent</dt><dd>${inc.agent || '—'}</dd>
        <dt>Type</dt><dd><strong>${(inc.type||'').replace(/_/g,' ')}</strong></dd>
        <dt>Status at detection</dt><dd>${pill('open','pill-red')}</dd>
        ${detectedTs ? `<dt>Time</dt><dd>${detectedTs}</dd>` : ''}
      </dl>
    `);

    // ── Step 2: Diagnosis ─────────────────────────────────────────────────
    if (diag.root_cause) {
      const diagTs = inc.diagnosed_at && inc.diagnosed_at !== "None" ? new Date(inc.diagnosed_at).toLocaleTimeString() : '';
      steps += tlItem('yellow', 'troubleshoot', 'Diagnosed by Gemini Flash', `
        <dl class="kv">
          <dt>Root cause</dt><dd>${diag.root_cause}</dd>
          <dt>Severity</dt><dd>${pill(diag.severity||'—', sevCls(diag.severity))}</dd>
          <dt>Confidence</dt><dd><strong>${((diag.confidence||0)*100).toFixed(0)}%</strong></dd>
          <dt>Recommended action</dt><dd>${diag.recommended_action||'—'}</dd>
          ${diagTs ? `<dt>Time</dt><dd>${diagTs}</dd>` : ''}
        </dl>
      `);
    } else {
      steps += tlItem('yellow', 'hourglass_empty', 'Diagnosis', `
        <div class="tl-content" style="color:var(--text-muted)">
          ${ status === 'open'
            ? '⏳ Awaiting Gemini analysis — the ADK supervisor will diagnose this incident shortly.'
            : 'No diagnosis data recorded.' }
        </div>
      `);
    }

    // ── Step 3: Governance decision ────────────────────────────────────────
    if (dec.action) {
      const decCls = dec.action === 'quarantine' ? 'pill-red'
                   : dec.action === 'retry'      ? 'pill-green'
                   : 'pill-yellow';
      steps += tlItem('blue', 'policy', 'Governance Decision Applied', `
        <dl class="kv">
          <dt>Action</dt><dd>${pill(dec.action.toUpperCase(), decCls)}</dd>
          <dt>Reason</dt><dd>${dec.reason||'—'}</dd>
          <dt>Approver</dt><dd>${dec.approver || (inc.auto_approve ? 'Auto-Approved (AEGIS_AUTO_APPROVE=true)' : 'Pending Human Review')}</dd>
        </dl>
      `);
    } else if (diag.root_cause) {
      steps += tlItem('blue', 'hourglass_empty', 'Governance Decision', `
        <div class="tl-content" style="color:var(--text-muted)">⏳ Awaiting policy engine decision.</div>
      `);
    }

    // ── Step 4: Remediation ────────────────────────────────────────────────
    if (rem.outcome) {
      const remIconColor = rem.outcome === 'success' ? 'green' : rem.outcome === 'skipped' ? 'yellow' : 'red';
      const remPillCls   = rem.outcome === 'success' ? 'pill-green' : rem.outcome === 'skipped' ? 'pill-yellow' : 'pill-red';
      steps += tlItem(remIconColor, 'build', 'Remediator Executed', `
        <dl class="kv">
          <dt>Outcome</dt><dd>${pill(rem.outcome.toUpperCase(), remPillCls)}</dd>
          <dt>Detail</dt><dd>${rem.detail||'—'}</dd>
        </dl>
      `);
    } else if (dec.action) {
      steps += tlItem('red', 'hourglass_empty', 'Remediation', `
        <div class="tl-content" style="color:var(--text-muted)">⏳ Awaiting remediator execution.</div>
      `);
    }

    // ── Step 5: Postmortem ─────────────────────────────────────────────────
    if (pm) {
      const pmHtml = pm.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\n/g,'<br>');
      steps += tlItem('green', 'description', 'Postmortem Report Generated', `<div class="tl-content">${pmHtml}</div>`);
    } else if (rem.outcome === 'success') {
      steps += tlItem('green', 'hourglass_empty', 'Postmortem', `
        <div class="tl-content" style="color:var(--text-muted)">⏳ Generating postmortem report…</div>
      `);
    }

    // ── Audit trail (extra entries beyond detected) ────────────────────────
    const extraAudit = audit.filter(e => e.actor !== 'detector' && e.action !== 'detected');
    if (extraAudit.length > 0) {
      const auditHtml = extraAudit.map(e => `
        <div style="display:flex;gap:8px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.04);font-size:.8rem;">
          <span style="color:var(--text-muted);min-width:90px;">${e.actor||''}</span>
          <span style="color:var(--google-blue);">${e.action||''}</span>
          <span style="color:var(--text-main);flex:1">${e.detail||''}</span>
        </div>
      `).join('');
      steps += tlItem('grey', 'receipt_long', 'Audit Trail', `
        <div style="border:1px solid var(--border);border-radius:8px;padding:8px;margin-top:4px;max-height:120px;overflow-y:auto;">
          ${auditHtml}
        </div>
      `);
    }

    const sev = diag.severity || '—';
    document.getElementById('modal-body').innerHTML = `
      <div class="modal-title">${(inc.type||'incident').replace(/_/g,' ')} — ${inc.agent||'unknown'}</div>
      <div class="modal-subtitle">
        ID: <code style="font-size:.75rem;opacity:.7">${inc.incident_id||inc.id||'—'}</code>
        &nbsp;·&nbsp;
        ${pill(status, statCls(status))}
        &nbsp;
        ${sev !== '—' ? pill(sev, sevCls(sev)) : ''}
      </div>
      <div class="timeline">${steps}</div>
    `;

    // Progressive reveal animation
    const stepEls = document.querySelectorAll('#modal-body .tl-item');
    stepEls.forEach((el, i) => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(10px)';
      el.style.transition = 'all 0.4s ease';
      setTimeout(() => {
        el.style.opacity = '1';
        el.style.transform = 'translateY(0)';
      }, 100 + (i * 600));
    });
  }

  function tlItem(color, icon, label, content) {
    return `
      <div class="tl-item">
        <div class="tl-icon ${color}">
          <span class="material-symbols-outlined" style="font-size:14px">${icon}</span>
        </div>
        <div class="tl-body">
          <div class="tl-label">${label}</div>
          <div class="tl-content">${content}</div>
        </div>
      </div>`;
  }

  function triggerIncident() {
    if(!confirm("Inject a simulated PII leak incident?")) return;
    fetch('/api/trigger', { method: 'POST' })
      .then(r => r.json())
      .then(d => {
         if(d.error) alert("Error: " + d.error);
         else alert("Incident triggered successfully! It will appear shortly.");
      })
      .catch(e => alert("Error triggering incident"));
  }

  // ── Chatbot Functions ─────────────────────────────────────────────────────
  function toggleChat() {
    const widget = document.getElementById('chat-widget');
    widget.classList.toggle('open');
  }

  function handleChatEnter(e) {
    if (e.key === 'Enter') sendChatMessage();
  }

  async function sendChatMessage() {
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) return;
    
    input.value = '';
    const msgs = document.getElementById('chat-messages');
    
    // Add user message
    msgs.insertAdjacentHTML('beforeend', `<div class="msg user">${text}</div>`);
    msgs.scrollTop = msgs.scrollHeight;
    
    // Add loading indicator
    const loadingId = 'loading-' + Date.now();
    msgs.insertAdjacentHTML('beforeend', `<div class="msg bot" id="${loadingId}">
      <span class="material-symbols-outlined" style="animation: spin 1s linear infinite; font-size: 16px;">sync</span>
    </div>`);
    msgs.scrollTop = msgs.scrollHeight;
    
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text })
      });
      
      const data = await res.json();
      document.getElementById(loadingId).remove();
      
      if (res.ok) {
        // Convert basic markdown
        let html = data.reply
            .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            .replace(/\n/g, '<br>')
            .replace(/- (.*?)(<br>|$)/g, '<ul><li>$1</li></ul>')
            .replace(/<\/ul><ul>/g, ''); // Fix consecutive ul tags
            
        msgs.insertAdjacentHTML('beforeend', `<div class="msg bot">${html}</div>`);
      } else {
        msgs.insertAdjacentHTML('beforeend', `<div class="msg bot" style="color:var(--google-red)">Error: ${data.error}</div>`);
      }
    } catch (err) {
      document.getElementById(loadingId).remove();
      msgs.insertAdjacentHTML('beforeend', `<div class="msg bot" style="color:var(--google-red)">Network error</div>`);
    }
    
    msgs.scrollTop = msgs.scrollHeight;
  }
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------

def get_dashboard_data() -> tuple[list, list, dict]:
    """Fetch agent stats, recent incidents, and summary metrics from Firestore.

    Returns:
        Tuple of (agents list, incidents list, metrics dict).
    """
    db = firestore.Client(project=PROJECT_ID)
    docs = (
        db.collection(COLLECTION)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(100)
        .stream()
    )

    incidents: list[dict] = []
    active_counts: dict = defaultdict(int)
    total_counts: dict = defaultdict(int)

    total_incidents = 0
    resolved_incidents = 0
    diagnose_times: list[float] = []
    tokens_flagged = 0
    cost_flagged = 0.0

    for doc in docs:
        data = doc.to_dict()
        agent = data.get("agent", "?")
        status = data.get("status", "?")
        diagnosis = data.get("diagnosis", {})
        decision = data.get("decision", {})
        raw_event = data.get("raw_event", {})

        total_counts[agent] += 1
        if status in ACTIVE_STATUSES:
            active_counts[agent] += 1

        total_incidents += 1
        if status in ("resolved", "escalated"):
            resolved_incidents += 1

        created_at = data.get("created_at")
        diagnosed_at = data.get("diagnosed_at")
        if created_at and diagnosed_at:
            try:
                delta = (diagnosed_at - created_at).total_seconds()
                if 0 < delta < 3600:
                    diagnose_times.append(delta)
            except Exception:
                pass

        tokens_flagged += raw_event.get("tokens", 0)
        cost_flagged += raw_event.get("cost", 0.0)

        incidents.append({
            "incident_id": data.get("incident_id", doc.id),
            "agent": agent,
            "type": data.get("type", "?"),
            "severity": diagnosis.get("severity", "-"),
            "action": decision.get("action", ""),
            "status": status,
        })

    agents = [
        {
            "name": name,
            "active_count": active_counts.get(name, 0),
            "total_count": total_counts.get(name, 0),
        }
        for name in AGENTS
    ]

    auto_resolved_pct = (
        round((resolved_incidents / total_incidents) * 100)
        if total_incidents else 0
    )

    metrics = {
        "total_incidents": total_incidents,
        "auto_resolved_pct": auto_resolved_pct,
        "mttd": compute_mttd(diagnose_times),
        "tokens_flagged": tokens_flagged,
        "cost_flagged": cost_flagged,
    }

    return agents, incidents[:50], metrics


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Render the main dashboard page."""
    try:
        agents, incidents, metrics = get_dashboard_data()
    except Exception as exc:
        logger.error("Failed to load dashboard data: %s", exc)
        agents, incidents = [], []
        metrics = {
            "total_incidents": 0,
            "auto_resolved_pct": 0,
            "mttd": "\u2014",
            "tokens_flagged": 0,
            "cost_flagged": 0.0,
        }
    return render_template_string(
        HTML, agents=agents, incidents=incidents, metrics=metrics,
        project_id=PROJECT_ID,
    )


@app.route("/incident/<incident_id>")
def incident_detail(incident_id: str):
    """Return full incident detail as JSON for the Review modal.

    Args:
        incident_id: Firestore document ID.
    """
    db = _get_db()  # reuse cached client — no new connection per request
    doc = db.collection(COLLECTION).document(incident_id).get()
    if not doc.exists:
        return jsonify({"error": "not found"}), 404

    data = doc.to_dict()

    # Serialise all Firestore timestamp objects (not JSON-serialisable).
    def _ts(val):
        if val is None:
            return None
        if hasattr(val, "isoformat"):
            return val.isoformat()
        return str(val)

    data["created_at"]   = _ts(data.get("created_at"))
    data["diagnosed_at"] = _ts(data.get("diagnosed_at"))

    # Serialise timestamps inside audit_log entries.
    for entry in data.get("audit_log", []):
        if isinstance(entry, dict) and "ts" in entry:
            entry["ts"] = _ts(entry["ts"])

    data.setdefault("incident_id", incident_id)
    return jsonify(data)

@app.route("/api/trigger", methods=["POST"])
def trigger_incident():
    """Trigger a simulated PII-leak incident by pushing directly to the local detector.

    This bypasses Google Cloud Pub/Sub so the full triage pipeline can be
    tested locally without any cloud routing.
    """
    try:
        event = {
            "event_id": str(uuid.uuid4()),
            "agent": "support-agent",
            "status": "success",
            "tokens": 800,
            "cost": 0.10,
            "tool_call": "lookup_customer",
            "confidence": 0.95,
            "pii_leak": True,
            "timestamp": time.time(),
        }
        # Wrap in a standard Pub/Sub push envelope so detector.py can decode it.
        payload = json.dumps(event).encode("utf-8")
        envelope = {"message": {"data": base64.b64encode(payload).decode("utf-8")}}

        detector_url = os.environ.get("DETECTOR_URL", "http://localhost:8080")
        resp = http_requests.post(
            f"{detector_url}/pubsub",
            json=envelope,
            timeout=60,
        )
        if resp.ok:
            return jsonify({"status": "Incident injected and triage pipeline triggered"}), 200
        else:
            return jsonify({"error": f"Detector returned {resp.status_code}: {resp.text}"}), 502
    except Exception as exc:
        logger.error("trigger_incident error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/healthz")
def healthz():
    """Health-check endpoint."""
    return jsonify({"healthy": True})


@app.route("/api/chat", methods=["POST"])
def chat():
    """Reporting Agent: Compiles incidents from Firestore and uses Gemini to generate a report."""
    try:
        user_msg = request.json.get("message", "")
        if not user_msg:
            return jsonify({"error": "Empty message"}), 400
        
        # 1. Invoke Tool: Fetch all incidents from Firestore
        db = _get_db()
        docs = db.collection(COLLECTION).stream()
        
        # 2. Compile data context for the agent
        compiled_data = []
        for doc in docs:
            d = doc.to_dict()
            compiled_data.append({
                "id": d.get("incident_id", doc.id),
                "agent": d.get("agent"),
                "type": d.get("type"),
                "status": d.get("status"),
                "severity": d.get("diagnosis", {}).get("severity", "unknown")
            })
            
        import json
        context_str = json.dumps(compiled_data, indent=2)
        
        # 3. Use Gemini to reason over the data and generate the report
        from google import genai
        client = genai.Client()
        model_name = os.environ.get("MODEL", "gemini-3.1-flash-lite")
        
        prompt = (
            f"You are the Aegis Reporting Agent. You have access to the live incident database.\n"
            f"Here is the current live data of all incidents in the system:\n"
            f"```json\n{context_str}\n```\n\n"
            f"The user has asked: '{user_msg}'\n\n"
            f"Write a concise, professional report or answer based ONLY on the data provided above. "
            f"Use markdown formatting, bullet points, and highlight key metrics. "
            f"Do not hallucinate any data not in the JSON."
        )
        
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
        )
        
        return jsonify({"reply": response.text}), 200
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Start the Flask development server."""
    port = int(os.environ.get("PORT", 8080))
    logger.info("Aegis Dashboard starting on http://localhost:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
