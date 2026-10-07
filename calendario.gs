/**
 * Ponte tra Jarvis e Google Calendar.
 * Si installa su https://script.google.com come "App web": esegui come "Me", accesso "Chiunque".
 * Senza il TOKEN giusto non risponde, quindi l'URL da solo non basta per leggere il calendario.
 */
const TOKEN = 'METTI_QUI_CALENDARIO_TOKEN';

function doPost(e) {
  let risposta;
  try {
    const dati = JSON.parse(e.postData.contents);
    if (dati.token !== TOKEN) {
      risposta = { errore: 'token non valido' };
    } else {
      risposta = AZIONI[dati.azione] ? AZIONI[dati.azione](dati) : { errore: 'azione sconosciuta: ' + dati.azione };
    }
  } catch (err) {
    risposta = { errore: String(err) };
  }
  return ContentService.createTextOutput(JSON.stringify(risposta)).setMimeType(ContentService.MimeType.JSON);
}

function descrivi(ev, cal) {
  return {
    id: ev.getId(),
    titolo: ev.getTitle(),
    inizio: ev.getStartTime().toISOString(),
    fine: ev.getEndTime().toISOString(),
    tutto_il_giorno: ev.isAllDayEvent(),
    luogo: ev.getLocation(),
    calendario: cal.getName(),
  };
}

const AZIONI = {
  ping: () => ({ ok: true, calendario: CalendarApp.getDefaultCalendar().getName() }),

  eventi: (d) => {
    const da = new Date(d.da), a = new Date(d.a);
    const opzioni = d.cerca ? { search: d.cerca } : {};
    let elenco = [];
    CalendarApp.getAllCalendars().forEach((cal) => {
      if (cal.isHidden()) return;
      cal.getEvents(da, a, opzioni).forEach((ev) => elenco.push(descrivi(ev, cal)));
    });
    elenco.sort((x, y) => x.inizio.localeCompare(y.inizio));
    return { eventi: elenco.slice(0, 50) };
  },

  crea: (d) => {
    const cal = CalendarApp.getDefaultCalendar();
    const opzioni = { location: d.luogo || '', description: d.descrizione || '' };
    const ev = d.tutto_il_giorno
      ? cal.createAllDayEvent(d.titolo, new Date(d.inizio), opzioni)
      : cal.createEvent(d.titolo, new Date(d.inizio), new Date(d.fine), opzioni);
    return { creato: descrivi(ev, cal) };
  },

  sposta: (d) => {
    const ev = CalendarApp.getEventById(d.id);
    if (!ev) return { errore: 'evento non trovato' };
    ev.setTime(new Date(d.inizio), new Date(d.fine));
    if (d.titolo) ev.setTitle(d.titolo);
    return { spostato: descrivi(ev, CalendarApp.getCalendarById(ev.getOriginalCalendarId())) };
  },

  cancella: (d) => {
    const ev = CalendarApp.getEventById(d.id);
    if (!ev) return { errore: 'evento non trovato' };
    const titolo = ev.getTitle();
    ev.deleteEvent();
    return { cancellato: titolo };
  },
};

/** Esegui questa funzione una volta dall'editor per concedere l'accesso al calendario. */
function autorizza() {
  Logger.log(AZIONI.ping());
}
