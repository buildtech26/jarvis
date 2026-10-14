// La chat di Jarvis nel browser (web.py). Tutto il testo entra con textContent: niente HTML dalle risposte.
"use strict";

const $ = (id) => document.getElementById(id);
let ultimo = 0;          // id dell'ultimo scambio mostrato
let attesa = null;       // la bolla della domanda in corso
let giro = null;         // timer dell'interrogazione periodica

async function api(percorso, corpo) {
  const opzioni = corpo === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corpo),
  };
  const r = await fetch(percorso, { credentials: "same-origin", ...opzioni });
  const dati = await r.json().catch(() => ({}));
  if (r.status === 401 && percorso !== "/api/accedi") {
    mostraAccesso();
    throw new Error("accesso richiesto");
  }
  return { ok: r.ok, dati };
}

// testo con i link cliccabili, senza mai interpretare HTML
function conLink(elemento, testo) {
  const parti = testo.split(/(https?:\/\/[^\s)]+)/g);
  for (const parte of parti) {
    if (/^https?:\/\//.test(parte)) {
      const a = document.createElement("a");
      a.href = parte; a.textContent = parte; a.target = "_blank"; a.rel = "noopener noreferrer";
      elemento.append(a);
    } else if (parte) {
      elemento.append(document.createTextNode(parte));
    }
  }
}

function bolla(chi, testo, nota) {
  const div = document.createElement("div");
  div.className = `bolla ${chi}`;
  if (nota) {
    const n = document.createElement("div");
    n.className = "nota"; n.textContent = nota;
    div.append(n);
  }
  const corpo = document.createElement("div");
  conLink(corpo, testo);
  div.append(corpo);
  $("messaggi").append(div);
  $("messaggi").scrollTop = $("messaggi").scrollHeight;
  return div;
}

async function aggiorna() {
  try {
    const { ok, dati } = await api(`/api/messaggi?dopo=${ultimo}`);
    if (!ok) return;
    for (const s of dati.messaggi) {
      if (attesa && s.canale === "web" && s.domanda === attesa.dataset.testo) {
        attesa.remove();
        attesa = null;
      }
      const nota = s.canale === "web" ? s.ora : `${s.ora} · ${s.canale}`;
      bolla("tu", s.domanda, nota);
      bolla("jarvis", s.risposta);
      ultimo = s.id;
    }
    $("fase").textContent = dati.fase || "";
  } catch (e) { /* rete assente o sessione scaduta: riprova al giro dopo */ }
}

function mostraChat() {
  $("accesso").hidden = true; $("chat").hidden = false; $("esci").hidden = false;
  aggiorna();
  clearInterval(giro);
  giro = setInterval(aggiorna, 3000);
  $("testo").focus();
}

function mostraAccesso() {
  clearInterval(giro);
  $("chat").hidden = true; $("esci").hidden = true; $("accesso").hidden = false;
  $("messaggi").replaceChildren();
  ultimo = 0;
  $("password").focus();
}

$("modulo-accesso").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("errore-accesso").textContent = "";
  const { ok, dati } = await api("/api/accedi", { password: $("password").value });
  $("password").value = "";
  if (ok) mostraChat(); else $("errore-accesso").textContent = dati.errore || "Accesso non riuscito.";
});

$("modulo-chat").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const testo = $("testo").value.trim();
  if (!testo || attesa) return;
  $("testo").value = "";
  adatta();
  $("invia").disabled = true;
  attesa = bolla("tu attesa", testo);
  attesa.dataset.testo = testo;
  $("fase").textContent = "sto pensando…";
  try {
    const { ok, dati } = await api("/api/chiedi", { testo });
    if (!ok) bolla("jarvis", dati.errore || "Qualcosa è andato storto.");
    await aggiorna();
  } catch (e) {
    bolla("jarvis", "Jarvis non risponde: il PC potrebbe essere spento o in standby.");
  } finally {
    if (attesa) { attesa.classList.remove("attesa"); attesa = null; }
    $("invia").disabled = false;
    $("testo").focus();
  }
});

// Invio manda, Maiusc+Invio va a capo; il campo cresce con il testo
$("testo").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); $("modulo-chat").requestSubmit(); }
});
function adatta() {
  const t = $("testo");
  t.style.height = "auto";
  t.style.height = `${t.scrollHeight + 2}px`;
  t.style.overflowY = t.scrollHeight > t.clientHeight + 2 ? "auto" : "hidden";
}
$("testo").addEventListener("input", adatta);

$("esci").addEventListener("click", async () => {
  await api("/api/esci", {}).catch(() => {});
  mostraAccesso();
});

// all'apertura: dentro se la sessione vale ancora, se no la pagina di accesso
api("/api/messaggi?dopo=0").then(({ ok }) => (ok ? mostraChat() : mostraAccesso())).catch(() => {});
