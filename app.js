const state = { events: [], filtered: [], selectedId: null, language: 'it', map: null, markers: [] };
const $ = (id) => document.getElementById(id);

const labels = {
  it: { events: 'eventi', noEvents: 'Nessun evento trovato con questi filtri.', free: 'Gratis', paid: 'A pagamento', booking: 'Prenotazione', rules: 'Regole', transport: 'Come arrivare', source: 'Fonte', verify: 'Verifica dettagli', maps: 'Indicazioni', updated: 'Ultimo aggiornamento' },
  en: { events: 'events', noEvents: 'No events match these filters.', free: 'Free', paid: 'Paid', booking: 'Booking', rules: 'Rules', transport: 'Getting there', source: 'Source', verify: 'Check details', maps: 'Directions', updated: 'Last updated' }
};

async function loadEvents() {
  const response = await fetch(`data/events.json?ts=${Date.now()}`);
  const data = await response.json();
  state.events = data.events || [];
  $('lastUpdated').textContent = `${labels[state.language].updated}: ${new Date(data.updatedAt).toLocaleString(state.language === 'it' ? 'it-IT' : 'en-GB')}`;
  applyFilters();
}

function isThisWeekend(date) {
  const now = new Date();
  const day = now.getDay();
  const saturday = new Date(now);
  saturday.setDate(now.getDate() + ((6 - day + 7) % 7));
  saturday.setHours(0,0,0,0);
  const monday = new Date(saturday);
  monday.setDate(saturday.getDate() + 2);
  return date >= saturday && date < monday;
}

function applyFilters() {
  const dateMode = $('dateFilter').value;
  const category = $('categoryFilter').value;
  const price = $('priceFilter').value;
  const query = $('searchInput').value.trim().toLowerCase();
  const today = new Date(); today.setHours(0,0,0,0);

  state.filtered = state.events.filter((event) => {
    const date = new Date(event.start);
    const eventDay = new Date(date); eventDay.setHours(0,0,0,0);
    const dateOk = dateMode === 'all' ||
      (dateMode === 'today' && eventDay.getTime() === today.getTime()) ||
      (dateMode === 'weekend' && isThisWeekend(date)) ||
      (dateMode === 'evening' && date.getHours() >= 18);
    const categoryOk = category === 'all' || event.category === category;
    const priceOk = price === 'all' || (price === 'free' ? event.free : !event.free);
    const haystack = `${event.title} ${event.venue} ${event.city} ${event.description}`.toLowerCase();
    return dateOk && categoryOk && priceOk && (!query || haystack.includes(query));
  }).sort((a,b) => new Date(a.start) - new Date(b.start));

  renderEvents();
  renderMarkers();
}

function formatDay(date) {
  return new Intl.DateTimeFormat(state.language === 'it' ? 'it-IT' : 'en-GB', { weekday: 'long', day: 'numeric', month: 'long' }).format(date);
}

function renderEvents() {
  $('resultCount').textContent = `${state.filtered.length} ${labels[state.language].events}`;
  if (!state.filtered.length) {
    $('eventList').innerHTML = `<div class="empty">${labels[state.language].noEvents}</div>`;
    return;
  }
  const grouped = Object.groupBy(state.filtered, (event) => new Date(event.start).toISOString().slice(0,10));
  $('eventList').innerHTML = Object.entries(grouped).map(([dateKey, events]) => `
    <section class="day-group">
      <h3 class="day-title">${formatDay(new Date(`${dateKey}T12:00:00`))}</h3>
      ${events.map(eventCard).join('')}
    </section>`).join('');
  document.querySelectorAll('.event-card').forEach((button) => button.addEventListener('click', () => selectEvent(button.dataset.id)));
}

function eventCard(event) {
  const date = new Date(event.start);
  const price = event.free ? labels[state.language].free : event.price;
  return `<button class="event-card ${state.selectedId === event.id ? 'selected' : ''}" data-id="${event.id}">
    <span class="event-time">${date.toLocaleTimeString('it-IT', {hour:'2-digit', minute:'2-digit'})}</span>
    <span>
      <h3>${event.title}</h3>
      <p class="event-meta">${event.venue} · ${event.city}</p>
      <span class="tags"><span class="tag">${event.categoryLabel}</span><span class="tag">${price}</span>${event.age ? `<span class="tag">${event.age}</span>` : ''}</span>
    </span>
  </button>`;
}

function selectEvent(id) {
  state.selectedId = id;
  const event = state.events.find((item) => item.id === id);
  renderEvents();
  renderContext(event);
  const marker = state.markers.find((item) => item.eventId === id);
  if (marker) { state.map.setView(marker.getLatLng(), 14); marker.openPopup(); }
  if (window.innerWidth <= 760) switchPanel('contextPanel');
}

function renderContext(event) {
  if (!event) return;
  $('contextContent').innerHTML = `
    <h3>${event.title}</h3>
    <p class="event-meta">${new Date(event.start).toLocaleString(state.language === 'it' ? 'it-IT' : 'en-GB', {weekday:'long', day:'numeric', month:'long', hour:'2-digit', minute:'2-digit'})} · ${event.venue}, ${event.city}</p>
    <div class="context-row"><span class="context-label">Descrizione</span>${event.description}</div>
    <div class="context-row"><span class="context-label">Prezzo</span>${event.free ? labels[state.language].free : event.price}</div>
    <div class="context-row"><span class="context-label">${labels[state.language].booking}</span>${event.booking || 'Non indicata'}</div>
    <div class="context-row"><span class="context-label">${labels[state.language].rules}</span>${event.rules || 'Nessuna regola specifica indicata.'}</div>
    <div class="context-row"><span class="context-label">${labels[state.language].transport}</span>${event.transport || 'Consulta la mappa per il percorso.'}</div>
    <div class="context-row"><span class="context-label">${labels[state.language].source}</span>${event.sourceName} · verificato ${event.verifiedAt}</div>
    <div class="context-actions">
      <a class="button" href="${event.sourceUrl}" target="_blank" rel="noreferrer">${labels[state.language].verify}</a>
      <a class="button secondary" href="https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(`${event.venue}, ${event.city}`)}" target="_blank" rel="noreferrer">${labels[state.language].maps}</a>
    </div>`;
}

function initMap() {
  state.map = L.map('map').setView([45.554, 8.946], 11);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(state.map);
}

function renderMarkers() {
  state.markers.forEach((marker) => marker.remove());
  state.markers = state.filtered.filter(e => e.lat && e.lng).map((event) => {
    const marker = L.marker([event.lat, event.lng]).addTo(state.map);
    marker.eventId = event.id;
    marker.bindPopup(`<h3>${event.title}</h3><div>${event.venue} · ${new Date(event.start).toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit'})}</div>`);
    marker.on('click', () => selectEvent(event.id));
    return marker;
  });
}

function switchPanel(target) {
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('active-panel', p.id === target));
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.target === target));
  if (target === 'mapPanel') setTimeout(() => state.map.invalidateSize(), 100);
}

['dateFilter','categoryFilter','priceFilter'].forEach(id => $(id).addEventListener('change', applyFilters));
$('searchInput').addEventListener('input', applyFilters);
$('refreshButton').addEventListener('click', loadEvents);
$('langToggle').addEventListener('click', () => { state.language = state.language === 'it' ? 'en' : 'it'; $('langToggle').textContent = state.language === 'it' ? 'EN' : 'IT'; applyFilters(); if (state.selectedId) renderContext(state.events.find(e => e.id === state.selectedId)); });
document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => switchPanel(tab.dataset.target)));

initMap();
loadEvents().catch(() => { $('eventList').innerHTML = '<div class="empty">Impossibile caricare gli eventi.</div>'; });
