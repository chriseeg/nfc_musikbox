/* NFC-Karten-Manager – Custom Card für Home Assistant
 * Verwaltet NFC-Karten für die NFC-Musikbox: Karte → Medium → Scanner/Lautsprecher.
 * Läuft im HA-Frontend (Login, Rechte, Remote-Zugriff über Home Assistant).
 * Erzeugt pro Karte eine Automation aus dem Blueprint BP (Tonie-Logik:
 * Karte liegt = Musik läuft, Karte weg = Pause, Position wird pro Karte gemerkt).
 */
(() => {
  const BP = 'christian/nfc_musikbox_karte_tonie.yaml';
  const KEY = 'nfc_musikbox';
  const FRESH_MS = 120000;

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
  const errMsg = (e) => (e && (e.message || (e.error && e.error.message) || e.error)) || String(e);
  const ago = (iso) => {
    if (!iso) return 'noch nie gescannt';
    const d = (Date.now() - Date.parse(iso)) / 1000;
    if (isNaN(d)) return '';
    if (d < 45) return 'gerade eben';
    if (d < 3600) return `vor ${Math.max(1, Math.round(d / 60))} Min.`;
    if (d < 86400) return `vor ${Math.round(d / 3600)} Std.`;
    return new Date(iso).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' });
  };
  const fmtPos = (sec) => { const s = Math.max(0, Math.round(sec)); const h = Math.floor(s / 3600); const m = Math.floor((s % 3600) / 60); const r = String(s % 60).padStart(2, '0'); return h ? `${h}:${String(m).padStart(2, '0')}:${r}` : `${m}:${r}`; };
  const ic = (n, cls = '') => `<ha-icon class="${cls}" icon="mdi:${n}"></ha-icon>`;

  const CSS = `
  :host{display:block;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,Roboto,system-ui,sans-serif);
    --acc:var(--primary-color,#03a9f4);--bg2:var(--secondary-background-color,#f3f4f6);--card:var(--card-background-color,#fff);
    --line:var(--divider-color,rgba(127,127,127,.25));--mute:var(--secondary-text-color,#6b7280);--ok:var(--success-color,#2e9d57);--bad:var(--error-color,#db4437)}
  *{box-sizing:border-box}
  .wrap{max-width:760px;margin:0 auto;padding:16px 16px 96px}
  header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:4px 0 18px}
  h1{font-size:28px;line-height:1.1;margin:0;font-weight:700;letter-spacing:-.3px}
  h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--mute);margin:26px 4px 10px;font-weight:600}
  .sub{margin:4px 0 0;color:var(--mute);font-size:14px}
  button{font:inherit;color:inherit;cursor:pointer;border:0;background:none;padding:0}
  .icon-btn{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;background:var(--bg2);--mdc-icon-size:22px}
  .icon-btn:active,.tile:active,.btn:active{transform:scale(.98)}
  .btn{min-height:46px;padding:0 20px;border-radius:14px;font-weight:600;background:var(--bg2);display:inline-flex;align-items:center;justify-content:center;gap:8px;--mdc-icon-size:20px}
  .btn.primary{background:var(--acc);color:var(--text-primary-color,#fff)}
  .btn.danger{color:var(--bad)}
  .btn[disabled]{opacity:.45;pointer-events:none}
  .btn.small{min-height:38px;padding:0 14px;font-size:14px;border-radius:12px}
  .tile{display:flex;gap:14px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:12px;margin-bottom:10px;cursor:pointer;box-shadow:0 1px 2px rgba(0,0,0,.05);transition:transform .08s}
  .art{flex:none;width:64px;height:64px;border-radius:12px;background:var(--bg2);display:grid;place-items:center;overflow:hidden;color:var(--mute);--mdc-icon-size:28px}
  .art img{width:100%;height:100%;object-fit:cover;display:block}
  .meta{flex:1;min-width:0}
  .title{font-weight:650;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .media{color:var(--mute);font-size:14px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
  .chip{display:inline-flex;align-items:center;gap:4px;font-size:12px;padding:3px 9px;border-radius:99px;background:var(--bg2);color:var(--mute);--mdc-icon-size:14px}
  .play{flex:none;width:46px;height:46px;border-radius:50%;display:grid;place-items:center;background:var(--acc);color:var(--text-primary-color,#fff);--mdc-icon-size:24px}
  .new{border-color:var(--acc)}
  .new.fresh{animation:pulse 1.6s ease-out infinite;background:color-mix(in srgb,var(--acc) 8%,var(--card))}
  @keyframes pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--acc) 45%,transparent)}100%{box-shadow:0 0 0 14px transparent}}
  .badge{font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--acc)}
  .x{flex:none;width:36px;height:36px;border-radius:50%;display:grid;place-items:center;color:var(--mute);--mdc-icon-size:20px}
  .empty{text-align:center;padding:40px 20px;color:var(--mute);border:2px dashed var(--line);border-radius:20px;--mdc-icon-size:44px}
  .empty b{display:block;color:var(--primary-text-color);font-size:17px;margin:10px 0 6px}
  .warn{background:color-mix(in srgb,var(--bad) 10%,var(--card));border:1px solid var(--bad);padding:12px 14px;border-radius:14px;margin-bottom:14px;font-size:14px}
  .overlay{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:20;display:flex;align-items:flex-end;justify-content:center;animation:fade .15s}
  .overlay.top{z-index:30}
  @keyframes fade{from{opacity:0}}@keyframes up{from{transform:translateY(30px);opacity:0}}
  .sheet{background:var(--card);width:100%;max-width:640px;max-height:92vh;border-radius:24px 24px 0 0;display:flex;flex-direction:column;animation:up .2s ease-out}
  .sheet.full{height:92vh}
  @media(min-width:700px){.overlay{align-items:center}.sheet{border-radius:24px}}
  .sh-head{display:flex;align-items:center;gap:8px;padding:16px 16px 8px}
  .sh-head h3{margin:0;font-size:19px;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sh-body{padding:8px 16px 16px;overflow:auto;flex:1}
  .sh-foot{display:flex;gap:10px;padding:12px 16px calc(14px + env(safe-area-inset-bottom));border-top:1px solid var(--line)}
  .sh-foot .btn{flex:1}
  label.f{display:block;font-size:12px;font-weight:600;color:var(--mute);text-transform:uppercase;letter-spacing:.06em;margin:16px 2px 6px}
  input[type=text],input[type=number],select{width:100%;min-height:48px;padding:0 14px;border-radius:14px;border:1px solid var(--line);background:var(--bg2);color:var(--primary-text-color);font:inherit;font-size:16px}
  input:focus,select:focus{outline:2px solid var(--acc);outline-offset:-1px}
  .pick{display:flex;gap:12px;align-items:center;width:100%;text-align:left;padding:10px;border:1px solid var(--line);border-radius:14px;background:var(--bg2)}
  .pick .art{width:52px;height:52px}
  .sc{display:flex;gap:12px;align-items:center;width:100%;text-align:left;padding:12px 14px;border:1.5px solid var(--line);border-radius:14px;margin-bottom:8px;--mdc-icon-size:22px}
  .sc.on{border-color:var(--acc);background:color-mix(in srgb,var(--acc) 9%,var(--card))}
  .sc.off{opacity:.55}
  .sc small{display:block;color:var(--mute)}
  .sc .t{flex:1;min-width:0}
  .crumbs{display:flex;gap:4px;flex-wrap:wrap;font-size:13px;color:var(--mute);margin:2px 0 10px}
  .crumbs button{color:var(--acc);font-weight:600}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(108px,1fr));gap:10px}
  .g{border:1px solid var(--line);border-radius:14px;overflow:hidden;background:var(--card);text-align:left;display:flex;flex-direction:column}
  .g .gi{aspect-ratio:1;background:var(--bg2);display:grid;place-items:center;--mdc-icon-size:34px;color:var(--mute)}
  .g img{width:100%;height:100%;object-fit:cover;display:block}
  .g .gt{padding:8px 10px 4px;font-size:13px;font-weight:600;line-height:1.25;height:3.4em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .g .ga{padding:0 8px 8px;display:flex;gap:6px}
  .g .ga .btn{flex:1;min-height:34px;padding:0 8px;font-size:13px;border-radius:10px}
  .row{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px;margin-bottom:10px}
  .row .top{display:flex;align-items:center;gap:10px;margin-bottom:10px}
  .row .top b{flex:1}
  .hint{color:var(--mute);font-size:13px;margin:6px 2px 0;line-height:1.4}
  .seg{display:flex;gap:8px}
  .seg button{flex:1;padding:12px 10px;border:1.5px solid var(--line);border-radius:14px;text-align:left;line-height:1.25;font-weight:600;font-size:14px}
  .seg button small{display:block;font-weight:400;color:var(--mute);font-size:12px;margin-top:3px}
  .seg button.on{border-color:var(--acc);background:color-mix(in srgb,var(--acc) 9%,var(--card))}
  .swrow{display:flex;align-items:center;gap:12px;padding:12px 14px;border:1.5px solid var(--line);border-radius:14px;width:100%;text-align:left}
  .swrow .t{flex:1;min-width:0}.swrow small{display:block;color:var(--mute)}
  .sw{flex:none;width:46px;height:28px;border-radius:99px;background:var(--line);position:relative;transition:background .15s}
  .sw::after{content:"";position:absolute;top:3px;left:3px;width:22px;height:22px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.35);transition:transform .15s}
  .sw.on{background:var(--acc)}.sw.on::after{transform:translateX(18px)}
  .posbox{display:flex;align-items:center;gap:10px;padding:12px 14px;border-radius:14px;background:var(--bg2);--mdc-icon-size:22px}
  .posbox .t{flex:1;min-width:0}.posbox b{display:block}.posbox small{display:block;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .tile.off{opacity:.55}
  .toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#222;color:#fff;padding:12px 18px;border-radius:12px;z-index:40;font-size:14px;max-width:90vw;box-shadow:0 6px 20px rgba(0,0,0,.3)}
  .toast.err{background:var(--bad)}
  .spin{display:grid;place-items:center;padding:40px;color:var(--mute)}
  .center{text-align:center}
  `;

  class NfcKartenCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this.S = {
        view: 'cards', loading: true, error: null, bpOk: true,
        autos: {}, settings: { scanners: [] }, devices: [],
        sheet: null, picker: null, confirm: null, toast: null,
      };
      this._sig = '';
      this._thumbs = new Map();
      this._pendingSign = new Set();
    }

    setConfig(c) { this._config = c || {}; }
    getCardSize() { return 12; }
    static getStubConfig() { return {}; }

    get hass() { return this._hass; }
    set hass(h) {
      const first = !this._hass;
      this._hass = h;
      if (first) { this._boot(); return; }
      const sig = this._sigStr();
      if (sig !== this._sig) {
        this._sig = sig;
        if (!this.S.sheet && !this.S.picker && !this.S.loading) this.render();
      }
    }

    connectedCallback() {
      this._timer = setInterval(() => { if (!this.S.sheet && !this.S.picker && !this.S.loading && this.S.view === 'cards') this.render(); }, 30000);
    }
    disconnectedCallback() { clearInterval(this._timer); }

    ws(msg) { return this._hass.callWS(msg); }

    async _boot() {
      this.shadowRoot.addEventListener('click', (e) => this._click(e));
      this.shadowRoot.addEventListener('change', (e) => this._change(e));
      this.shadowRoot.addEventListener('input', (e) => this._input(e));
      this.render();
      await this.load();
      this.S.loading = false;
      this._sig = this._sigStr();
      this.render();
    }

    /* ---------- Daten ---------- */
    _tags() {
      const st = this._hass.states;
      return Object.values(st)
        .filter((s) => s.entity_id.startsWith('tag.') && s.attributes && s.attributes.tag_id)
        .map((s) => ({
          entity: s.entity_id, tag_id: s.attributes.tag_id,
          name: s.attributes.friendly_name || s.attributes.tag_id,
          last: s.state && !['unknown', 'unavailable'].includes(s.state) ? s.state : null,
        }));
    }
    _players() {
      return Object.values(this._hass.states).filter((s) => s.entity_id.startsWith('media_player.'))
        .map((s) => ({ id: s.entity_id, name: s.attributes.friendly_name || s.entity_id }))
        .sort((a, b) => a.name.localeCompare(b.name, 'de'));
    }
    _sensorFor(reader) {
      const ents = this._hass.entities || {};
      const re = /karte_auf_dem_reader$/;
      const c = Object.values(ents).filter((e) => e.device_id === reader && e.entity_id.startsWith('sensor.') && re.test(e.entity_id));
      return c.length ? c[0].entity_id : null;
    }
    _readerForSensor(sensor) {
      const e = (this._hass.entities || {})[sensor];
      return e ? e.device_id : null;
    }
    _pos(memory) {
      const st = memory && this._hass.states[memory];
      if (!st || !st.state || ['unknown', 'unavailable'].includes(st.state)) return null;
      try {
        const m = JSON.parse(st.state);
        if (!m || typeof m !== 'object') return null;
        const q = Number(m.q) || 0; const p = Number(m.p) || 0;
        if (!q && !p && !m.t) return null;
        return { q, p, t: m.t || '', main: `${q ? `Titel ${q} · ` : ''}${fmtPos(p)}` };
      } catch (e) { return null; }
    }
    _sigStr() {
      return this._tags().map((t) => {
        const a = this.S.autos[t.entity];
        return `${t.entity}|${t.last}|${t.name}|${a && a.memory && this._hass.states[a.memory] ? this._hass.states[a.memory].state : ''}|${a && this._hass.states[a.entity] ? this._hass.states[a.entity].state : ''}`;
      }).join(';');
    }
    _playerName(id) { const s = this._hass.states[id]; return (s && s.attributes.friendly_name) || id || '–'; }
    _devName(id) {
      const d = this.S.devices.find((x) => x.id === id) || (this._hass.devices && this._hass.devices[id]);
      return d ? (d.name_by_user || d.name || id) : 'Unbekanntes Gerät';
    }

    async load() {
      const S = this.S;
      try {
        const [entries, bps, sys] = await Promise.all([
          this.ws({ type: 'config_entries/get', domain: 'esphome' }).catch(() => []),
          this.ws({ type: 'blueprint/list', domain: 'automation' }).catch(() => null),
          this.ws({ type: 'frontend/get_system_data', key: KEY }).catch(() => null),
        ]);
        S.bpOk = bps ? Object.prototype.hasOwnProperty.call(bps, BP) : true;

        // ESPHome-Geräte
        const ids = new Set((entries || []).map((e) => e.entry_id));
        let devs = this._hass.devices ? Object.values(this._hass.devices) : null;
        if (!devs) devs = await this.ws({ type: 'config/device_registry/list' }).catch(() => []);
        S.devices = devs.filter((d) => (d.config_entries || []).some((c) => ids.has(c)))
          .map((d) => ({ id: d.id, name: d.name_by_user || d.name || d.id }));

        // Automationen aus dem Blueprint
        let autoEntities = null;
        try {
          const rel = await this.ws({ type: 'search/related', item_type: 'automation_blueprint', item_id: BP });
          autoEntities = rel.automation || [];
        } catch (e) {
          autoEntities = Object.keys(this._hass.states).filter((k) => k.startsWith('automation.'));
        }
        const autos = {};
        await Promise.all(autoEntities.map(async (ent) => {
          const st = this._hass.states[ent];
          const id = st && st.attributes && st.attributes.id;
          if (!id) return;
          try {
            const cfg = await this._hass.callApi('GET', `config/automation/config/${id}`);
            if (cfg && cfg.use_blueprint && cfg.use_blueprint.path === BP) {
              const inp = cfg.use_blueprint.input || {};
              if (inp.card) autos[inp.card] = { autoId: id, entity: ent, alias: cfg.alias, media: inp.media, memory: inp.memory || null, mode: inp.mode_select === 'simple' ? 'simple' : 'tonie', scanners: inp.scanners || [], on: st.state !== 'off' };
            }
          } catch (e) { /* keine Blueprint-Automation */ }
        }));
        S.autos = autos;

        // Einstellungen (Scanner ↔ Player)
        const val = (sys && sys.value) || {};
        const scanners = Array.isArray(val.scanners) ? val.scanners.map((x) => ({ ...x })) : [];
        Object.values(autos).forEach((a) => a.scanners.forEach((sc) => {
          const reader = sc && sc.tag_sensor ? this._readerForSensor(sc.tag_sensor) : null;
          if (reader && !scanners.some((x) => x.reader === reader)) scanners.push({ reader, player: sc.player });
        }));
        S.settings = { scanners: scanners.map(({ reader, player }) => ({ reader, player })) };
        S.error = null;
      } catch (e) { S.error = errMsg(e); }
    }

    async saveSettings() {
      const { scanners } = this.S.settings;
      await this.ws({ type: 'frontend/set_system_data', key: KEY, value: { scanners } });
    }

    toast(msg, err = false) {
      this.S.toast = { msg, err };
      this.render();
      clearTimeout(this._tt);
      this._tt = setTimeout(() => { this.S.toast = null; this.render(); }, err ? 6000 : 2200);
    }

    /* ---------- Thumbnails ---------- */
    thumb(url) {
      if (!url) return null;
      if (!url.startsWith('/')) return url;
      if (this._thumbs.has(url)) return this._thumbs.get(url);
      if (!this._pendingSign.has(url)) {
        this._pendingSign.add(url);
        this.ws({ type: 'auth/sign_path', path: url, expires: 3600 })
          .then((r) => this._thumbs.set(url, r.path))
          .catch(() => this._thumbs.set(url, null))
          .finally(() => { this._pendingSign.delete(url); clearTimeout(this._rt); this._rt = setTimeout(() => this.render(), 150); });
      }
      return null;
    }
    art(url, icon = 'music-note') {
      const t = this.thumb(url);
      return `<div class="art">${t ? `<img src="${esc(t)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ic(icon)}</div>`;
    }

    /* ---------- Rendering ---------- */
    render() {
      const S = this.S;
      let body;
      if (S.loading) body = `<div class="wrap"><div class="spin">Lade Karten …</div></div>`;
      else if (S.error) body = `<div class="wrap"><div class="warn"><b>Fehler beim Laden</b><br>${esc(S.error)}</div><button class="btn" data-a="reload">Erneut versuchen</button></div>`;
      else body = S.view === 'settings' ? this.vSettings() : this.vCards();
      const root = this.shadowRoot;
      const keep = root.querySelector('.sh-body');
      const scroll = keep ? keep.scrollTop : 0;
      root.innerHTML = `<style>${CSS}</style>${body}${this.vSheet()}${this.vPicker()}${this.vConfirm()}${S.toast ? `<div class="toast ${S.toast.err ? 'err' : ''}">${esc(S.toast.msg)}</div>` : ''}`;
      if (scroll) { const nb = root.querySelector('.sh-body'); if (nb) nb.scrollTop = scroll; }
    }

    vCards() {
      const S = this.S;
      const tags = this._tags();
      const assigned = tags.filter((t) => S.autos[t.entity]).sort((a, b) => a.name.localeCompare(b.name, 'de'));
      const fresh = tags.filter((t) => !S.autos[t.entity]).sort((a, b) => (Date.parse(b.last) || 0) - (Date.parse(a.last) || 0));
      const nSc = S.settings.scanners.length;
      let h = `<div class="wrap"><header><div><h1>Musikkarten</h1><p class="sub">${assigned.length} ${assigned.length === 1 ? 'Karte' : 'Karten'} · ${nSc} ${nSc === 1 ? 'Lesegerät' : 'Lesegeräte'}</p></div>
        <button class="icon-btn" data-a="settings" aria-label="Einstellungen">${ic('cog-outline')}</button></header>`;
      if (!S.bpOk) h += `<div class="warn"><b>Blueprint fehlt:</b> <code>${esc(BP)}</code> ist in Home Assistant nicht installiert. Karten können erst nach dem Anlegen funktionieren.</div>`;
      if (fresh.length) {
        h += `<h2>Noch ohne Musik</h2>`;
        fresh.forEach((t) => {
          const isFresh = t.last && Date.now() - Date.parse(t.last) < FRESH_MS;
          h += `<div class="tile new ${isFresh ? 'fresh' : ''}" data-a="new" data-e="${esc(t.entity)}">
            <div class="art">${ic('nfc-variant')}</div>
            <div class="meta">${isFresh ? '<div class="badge">Gerade gescannt</div>' : ''}<div class="title">${esc(t.name)}</div>
              <div class="media">${esc(ago(t.last))}</div>
              <div style="margin-top:10px"><button class="btn small primary" data-a="new" data-e="${esc(t.entity)}">Zuordnen</button></div></div>
            <button class="x" data-a="del-tag" data-e="${esc(t.entity)}" aria-label="Karte entfernen" style="align-self:flex-start">${ic('close')}</button></div>`;
        });
      }
      if (assigned.length || fresh.length) h += `<h2>Zugeordnete Karten</h2>`;
      if (!assigned.length && !fresh.length) {
        h += `<div class="empty">${ic('nfc-variant')}<b>Noch keine Karte</b>Lege eine Karte auf das Lesegerät.<br>Sie erscheint hier automatisch – dann wählst du ihre Musik aus.</div>`;
      } else if (!assigned.length) {
        h += `<div class="empty"><b>Noch nichts zugeordnet</b>Tippe oben bei einer Karte auf „Zuordnen“.</div>`;
      }
      assigned.forEach((t) => {
        const a = S.autos[t.entity];
        const m = a.media || {};
        const title = (m.metadata && m.metadata.title) || m.media_content_id || 'Kein Medium';
        const pos = a.mode === 'tonie' ? this._pos(a.memory) : null;
        const chips = a.scanners.map((sc) => `<span class="chip">${ic('speaker', '')}${esc(this._playerName(sc.player).replace(/^Sonos\s+/i, ''))}</span>`).join('');
        h += `<div class="tile ${a.on ? '' : 'off'}" data-a="edit" data-e="${esc(t.entity)}">
          ${this.art(m.metadata && m.metadata.thumbnail)}
          <div class="meta"><div class="title">${esc(t.name)}</div><div class="media">${esc(title)}</div>
            <div class="chips">${chips}<span class="chip">${ic('clock-outline')}${esc(ago(t.last))}</span>${a.on ? '' : `<span class="chip">${ic('power-off')}deaktiviert</span>`}${a.mode === 'simple' ? `<span class="chip">${ic('play-circle-outline')}einfach</span>` : ''}</div>
            ${pos ? `<div class="media" style="margin-top:6px">${ic('bookmark-outline')} ${esc(pos.main)}${pos.t ? ' · ' + esc(pos.t) : ''}</div>` : ''}</div>
          <button class="play" data-a="test" data-e="${esc(t.entity)}" aria-label="Zum Testen abspielen">${ic('play')}</button></div>`;
      });
      return h + `</div>`;
    }

    vSettings() {
      const S = this.S;
      const players = this._players();
      const used = new Set(S.settings.scanners.map((x) => x.reader));
      const free = S.devices.filter((d) => !used.has(d.id));
      let h = `<div class="wrap"><header><div><h1>Einstellungen</h1><p class="sub">Lesegeräte und Lautsprecher</p></div>
        <button class="icon-btn" data-a="back" aria-label="Zurück">${ic('arrow-left')}</button></header><h2>Lesegeräte</h2>`;
      if (!S.settings.scanners.length) h += `<div class="empty"><b>Noch kein Lesegerät</b>Füge unten dein ESPHome-Lesegerät hinzu.</div>`;
      S.settings.scanners.forEach((sc) => {
        h += `<div class="row"><div class="top">${ic('nfc-variant')}<b>${esc(this._devName(sc.reader))}</b>
          <button class="x" data-a="rm-scanner" data-r="${esc(sc.reader)}" aria-label="Entfernen">${ic('delete-outline')}</button></div>
          <label class="f" style="margin-top:0">Lautsprecher dieses Lesegeräts</label>
          <select data-ch="player" data-r="${esc(sc.reader)}"><option value="">– bitte wählen –</option>
          ${players.map((p) => `<option value="${esc(p.id)}" ${p.id === sc.player ? 'selected' : ''}>${esc(p.name)}</option>`).join('')}</select>
          ${this._sensorFor(sc.reader) ? '' : `<p class="hint" style="color:var(--bad)">Der Sensor „Karte auf dem Reader“ fehlt. Läuft auf dem Lesegerät die Firmware v3?</p>`}</div>`;
      });
      if (free.length) {
        h += `<h2>Lesegerät hinzufügen</h2>` + free.map((d) => `<button class="sc" data-a="add-scanner" data-d="${esc(d.id)}">${ic('plus-circle-outline')}<span class="t">${esc(d.name)}</span></button>`).join('')
          + `<p class="hint">Gezeigt werden alle ESPHome-Geräte.</p>`;
      }
      return h + `</div>`;
    }

    vSheet() {
      const sh = this.S.sheet;
      if (!sh) return '';
      const sc = this.S.settings.scanners;
      const m = sh.media;
      const title = m ? ((m.metadata && m.metadata.title) || m.media_content_id) : null;
      const ready = sc.filter((x) => x.player && this._sensorFor(x.reader));
      return `<div class="overlay" data-a="close-sheet"><div class="sheet" data-stop="1">
        <div class="sh-head"><h3>${sh.edit ? 'Karte bearbeiten' : 'Neue Karte'}</h3><button class="x" data-a="close-sheet" aria-label="Schließen">${ic('close')}</button></div>
        <div class="sh-body">
          <label class="f" style="margin-top:4px">Name</label>
          <input type="text" data-in="name" value="${esc(sh.name)}" placeholder="z. B. Hörspiel Puderzucker" autocomplete="off">
          <label class="f">Medium</label>
          <button class="pick" data-a="pick-media">${this.art(m && m.metadata && m.metadata.thumbnail)}
            <span class="meta"><span class="title" style="display:block">${esc(title || 'Medium auswählen')}</span>
            <span class="media" style="display:block">${m ? 'Tippen zum Ändern' : 'Album, Playlist, Favorit, Radio …'}</span></span>${ic('chevron-right')}</button>
          <label class="f">Betriebsart</label>
          <div class="seg">
            <button class="${sh.mode === 'tonie' ? 'on' : ''}" data-a="set-mode" data-m="tonie">Tonie<small>Läuft nur, solange die Karte liegt. Position wird gemerkt.</small></button>
            <button class="${sh.mode === 'simple' ? 'on' : ''}" data-a="set-mode" data-m="simple">Einfach<small>Karte startet die Wiedergabe, sonst nichts.</small></button>
          </div>
          ${sh.mode === 'tonie' && sh.edit ? (() => { const p = this._pos(sh.memory); return `<label class="f">Gemerkte Position</label><div class="posbox">${ic('bookmark-outline')}
            <span class="t">${p ? `<b>${esc(p.main)}</b><small>${esc(p.t || 'Titel unbekannt')}</small>` : '<b>Noch keine Position</b><small>Wird beim Herausziehen der Karte gespeichert.</small>'}</span>
            ${p ? '<button class="btn small" data-a="reset-pos">Zurücksetzen</button>' : ''}</div>`; })() : ''}
          <label class="f">Funktioniert an</label>
          ${sc.length ? sc.map((x) => `<button class="sc ${sh.sel.has(x.reader) ? 'on' : ''} ${x.player ? '' : 'off'}" data-a="toggle-scanner" data-r="${esc(x.reader)}">
            ${ic(sh.sel.has(x.reader) ? 'checkbox-marked-circle' : 'checkbox-blank-circle-outline')}
            <span class="t">${esc(this._devName(x.reader))}<small>${!this._sensorFor(x.reader) ? 'Sensor „Karte auf dem Reader“ fehlt (Einstellungen)' : x.player ? '→ ' + esc(this._playerName(x.player)) : 'Kein Lautsprecher gewählt (Einstellungen)'}</small></span></button>`).join('')
          : `<p class="hint">Noch kein Lesegerät eingerichtet. <button class="btn small" data-a="to-settings">Zu den Einstellungen</button></p>`}
          ${sh.edit ? `<label class="f">Karte</label><button class="swrow" data-a="toggle-enabled"><span class="t">Karte aktiv<small>${sh.enabled ? 'Reagiert auf das Auflegen.' : 'Deaktiviert: Auflegen bewirkt nichts.'}</small></span><span class="sw ${sh.enabled ? 'on' : ''}"></span></button>` : ''}
          ${sh.error ? `<div class="warn" style="margin-top:12px">${esc(sh.error)}</div>` : ''}
        </div>
        <div class="sh-foot">
          ${sh.edit ? `<button class="btn danger" data-a="remove-assign" style="flex:0 0 auto">${ic('delete-outline')}</button>` : ''}
          <button class="btn primary" data-a="save" ${sh.saving || !ready.length ? 'disabled' : ''}>${sh.saving ? 'Speichere …' : 'Speichern'}</button>
        </div></div></div>`;
    }

    vPicker() {
      const p = this.S.picker;
      if (!p) return '';
      const q = (p.filter || '').toLowerCase();
      const items = (p.items || []).filter((i) => !q || (i.title || '').toLowerCase().includes(q));
      const crumbs = [`<button data-a="crumb" data-i="-1">Start</button>`].concat(p.stack.map((s, i) => i === p.stack.length - 1
        ? `<span>${esc(s.title)}</span>` : `<button data-a="crumb" data-i="${i}">${esc(s.title)}</button>`));
      let list;
      if (p.loading) list = `<div class="spin">Lade …</div>`;
      else if (p.error) list = `<div class="warn">${esc(p.error)}</div>`;
      else if (!items.length) list = `<div class="empty"><b>Nichts gefunden</b></div>`;
      else list = `<div class="grid">${items.map((i) => {
        const idx = p.items.indexOf(i);
        const folder = i.can_expand;
        return `<div class="g"><button class="gi" style="width:100%" data-a="${folder ? 'open' : 'choose'}" data-i="${idx}">
          ${this.thumb(i.thumbnail) ? `<img src="${esc(this.thumb(i.thumbnail))}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ic(folder ? 'folder-music-outline' : 'music-note')}</button>
          <div class="gt">${esc(i.title)}</div>
          <div class="ga">${folder ? `<button class="btn small" data-a="open" data-i="${idx}">Öffnen</button>` : ''}${i.can_play ? `<button class="btn small primary" data-a="choose" data-i="${idx}">Wählen</button>` : ''}</div></div>`;
      }).join('')}</div>`;
      return `<div class="overlay top"><div class="sheet full" data-stop="1">
        <div class="sh-head"><button class="x" data-a="close-picker" aria-label="Zurück">${ic('arrow-left')}</button><h3>Medium wählen</h3></div>
        <div class="sh-body"><div class="crumbs">${crumbs.join('<span>›</span>')}</div>
          <input type="text" data-in="filter" placeholder="Filtern …" value="${esc(p.filter || '')}" style="margin-bottom:12px">${list}</div></div></div>`;
    }

    vConfirm() {
      const c = this.S.confirm;
      if (!c) return '';
      return `<div class="overlay top" data-a="confirm-no"><div class="sheet" data-stop="1" style="border-radius:24px;margin:16px;width:auto">
        <div class="sh-head"><h3>${esc(c.title)}</h3></div><div class="sh-body"><p class="hint" style="font-size:15px">${esc(c.text)}</p></div>
        <div class="sh-foot"><button class="btn" data-a="confirm-no">Abbrechen</button><button class="btn primary" data-a="confirm-yes">${esc(c.ok || 'OK')}</button></div></div></div>`;
    }

    /* ---------- Events ---------- */
    _input(e) {
      const k = e.target.dataset && e.target.dataset.in;
      if (k === 'name' && this.S.sheet) this.S.sheet.name = e.target.value;
            if (k === 'filter' && this.S.picker) {
        this.S.picker.filter = e.target.value;
        const pos = e.target.selectionStart;
        this.render();
        const f = this.shadowRoot.querySelector('[data-in=filter]');
        if (f) { f.focus(); try { f.setSelectionRange(pos, pos); } catch (_) { /* ignore */ } }
      }
    }
    async _change(e) {
      const t = e.target;
      if (t.dataset.ch === 'player') {
        const sc = this.S.settings.scanners.find((x) => x.reader === t.dataset.r);
        if (!sc) return;
        sc.player = t.value;
        try { await this.saveSettings(); this.toast('Gespeichert'); } catch (err) { this.toast(errMsg(err), true); }
      }
    }

    async _click(e) {
      const el = e.target.closest('[data-a]');
      const inSheet = e.target.closest('[data-stop]');
      if (!el) return;
      if (el.classList.contains('overlay') && inSheet) return; // Klick im Sheet schließt nicht
      const a = el.dataset.a;
      const S = this.S;
      try {
        switch (a) {
          case 'reload': S.loading = true; S.error = null; this.render(); await this.load(); S.loading = false; this.render(); break;
          case 'settings': case 'to-settings': S.sheet = null; S.view = 'settings'; this.render(); break;
          case 'back': S.view = 'cards'; this.render(); break;
          case 'new': this.openSheet(el.dataset.e, false); break;
          case 'edit': this.openSheet(el.dataset.e, true); break;
          case 'close-sheet': if (el.classList.contains('overlay') && e.target !== el) return; S.sheet = null; this.render(); break;
          case 'toggle-scanner': {
            const sc = S.settings.scanners.find((x) => x.reader === el.dataset.r);
            if (!sc || !sc.player || !this._sensorFor(sc.reader)) { this.toast('Erst in den Einstellungen Lautsprecher und Sensor prüfen', true); break; }
            const s = S.sheet.sel; s.has(sc.reader) ? s.delete(sc.reader) : s.add(sc.reader); this.render(); break;
          }
          case 'pick-media': this.openPicker(); break;
          case 'close-picker': S.picker = null; this.render(); break;
          case 'crumb': await this.crumb(Number(el.dataset.i)); break;
          case 'open': await this.openItem(Number(el.dataset.i)); break;
          case 'choose': this.chooseItem(Number(el.dataset.i)); break;
          case 'save': await this.save(); break;
          case 'remove-assign':
            S.confirm = { title: 'Zuordnung löschen?', text: 'Die Karte bleibt in Home Assistant bestehen, spielt aber keine Musik mehr, bis du ihr wieder ein Medium gibst.', ok: 'Löschen', run: () => this.removeAssign() };
            this.render(); break;
          case 'del-tag': {
            const t = this._tags().find((x) => x.entity === el.dataset.e);
            S.confirm = { title: 'Karte entfernen?', text: `„${t ? t.name : ''}“ wird aus Home Assistant gelöscht. Beim nächsten Scannen taucht sie wieder auf.`, ok: 'Entfernen', run: () => this.delTag(t) };
            this.render(); break;
          }
          case 'confirm-no': if (el.classList.contains('overlay') && e.target !== el) return; S.confirm = null; this.render(); break;
          case 'confirm-yes': { const c = S.confirm; S.confirm = null; this.render(); if (c && c.run) await c.run(); break; }
          case 'test': await this.test(el.dataset.e); break;
          case 'add-scanner': await this.addScanner(el.dataset.d); break;
          case 'rm-scanner':
            S.confirm = { title: 'Lesegerät entfernen?', text: 'Es wird nur aus dieser Liste entfernt. Bestehende Karten behalten ihre Einstellung.', ok: 'Entfernen', run: async () => {
              S.settings.scanners = S.settings.scanners.filter((x) => x.reader !== el.dataset.r); await this.saveSettings(); this.render(); } };
            this.render(); break;
          case 'reset-pos': await this.resetPos(); break;
          case 'set-mode': S.sheet.mode = el.dataset.m; this.render(); break;
          case 'toggle-enabled': S.sheet.enabled = !S.sheet.enabled; this.render(); break;
          default: break;
        }
      } catch (err) { this.toast(errMsg(err), true); }
    }

    openSheet(entity, edit) {
      const t = this._tags().find((x) => x.entity === entity);
      if (!t) return;
      const a = this.S.autos[entity];
      const ready = this.S.settings.scanners.filter((x) => x.player && this._sensorFor(x.reader)).map((x) => x.reader);
      this.S.sheet = {
        edit, tag: t, name: t.name, media: a ? a.media : null, autoId: a ? a.autoId : null, alias: a ? a.alias : null,
        memory: a ? a.memory : null, mode: a ? a.mode : 'tonie', enabled: a ? a.on : true,
        sel: new Set(a ? a.scanners.map((x) => this._readerForSensor(x.tag_sensor)).filter(Boolean) : ready), error: null, saving: false,
      };
      this.render();
    }

    /* ---------- Medien-Browser ---------- */
    _browsePlayer() {
      const sh = this.S.sheet;
      const sc = this.S.settings.scanners.filter((x) => x.player);
      const sel = sc.find((x) => sh && sh.sel.has(x.reader)) || sc[0];
      return sel ? sel.player : null;
    }
    async openPicker() {
      const player = this._browsePlayer();
      if (!player) { this.toast('Erst in den Einstellungen einen Lautsprecher wählen', true); return; }
      this.S.picker = { player, stack: [], items: [], loading: true, error: null, filter: '' };
      this.render();
      await this.browse();
    }
    async browse() {
      const p = this.S.picker;
      p.loading = true; p.error = null; p.filter = ''; this.render();
      const cur = p.stack[p.stack.length - 1];
      const msg = { type: 'media_player/browse_media', entity_id: p.player };
      if (cur) { msg.media_content_type = cur.type; msg.media_content_id = cur.id; }
      try {
        const r = await this.ws(msg);
        p.items = (r.children || []).filter((c) => c.can_play || c.can_expand);
      } catch (e) { p.items = []; p.error = errMsg(e); }
      p.loading = false; this.render();
    }
    async openItem(i) {
      const p = this.S.picker; const it = p.items[i];
      p.stack.push({ title: it.title, type: it.media_content_type, id: it.media_content_id });
      await this.browse();
    }
    async crumb(i) { const p = this.S.picker; p.stack = p.stack.slice(0, i + 1); await this.browse(); }
    chooseItem(i) {
      const p = this.S.picker; const it = p.items[i];
      const meta = { title: it.title, thumbnail: it.thumbnail || null, media_class: it.media_class || null, children_media_class: it.children_media_class || null,
        navigateIds: [{}].concat(p.stack.map((s) => ({ media_content_type: s.type, media_content_id: s.id }))) };
      this.S.sheet.media = { entity_id: p.player, media_content_id: it.media_content_id, media_content_type: it.media_content_type, metadata: meta };
      this.S.picker = null;
      if (!this.S.sheet.name || /^Tag [0-9A-F-]+$/i.test(this.S.sheet.name)) this.S.sheet.name = it.title;
      this.render();
    }

    /* ---------- Aktionen ---------- */
    async save() {
      const sh = this.S.sheet;
      sh.name = (sh.name || '').trim();
      if (!sh.name) { sh.error = 'Bitte einen Namen eingeben.'; this.render(); return; }
      if (!sh.media) { sh.error = 'Bitte ein Medium auswählen.'; this.render(); return; }
      const scanners = this.S.settings.scanners.filter((x) => x.player && sh.sel.has(x.reader) && this._sensorFor(x.reader))
        .map((x) => ({ tag_sensor: this._sensorFor(x.reader), player: x.player }));
      if (!scanners.length) { sh.error = 'Bitte mindestens ein Lesegerät auswählen.'; this.render(); return; }
      sh.saving = true; sh.error = null; this.render();
      try {
        if (sh.name !== sh.tag.name) await this.ws({ type: 'tag/update', tag_id: sh.tag.tag_id, name: sh.name });
        let memory = sh.memory || '';
        if (!memory && sh.mode === 'tonie') {
          const r = await this.ws({ type: 'input_text/create', name: `NFC Position ${sh.name}`, max: 255, min: 0, icon: 'mdi:bookmark-music-outline' });
          memory = `input_text.${r.id}`;
        }
        const id = sh.autoId || `nfc_karte_${slug(sh.tag.tag_id)}_${Date.now().toString(36)}`;
        await this._hass.callApi('POST', `config/automation/config/${id}`, {
          alias: sh.alias || `Tag ${sh.name}`,
          description: 'Verwaltet vom NFC-Karten-Manager',
          use_blueprint: { path: BP, input: { card: sh.tag.entity, media: sh.media, mode_select: sh.mode, memory, scanners, tag_sensors: scanners.map((x) => x.tag_sensor) } },
        });
        await sleep(900);
        await this.load();
        const au = this.S.autos[sh.tag.entity];
        if (au && au.on !== sh.enabled) {
          await this._hass.callService('automation', sh.enabled ? 'turn_on' : 'turn_off', { entity_id: au.entity });
          await sleep(400); await this.load();
        }
        this.S.sheet = null;
        this.render();
        this.toast('Gespeichert');
      } catch (e) { sh.saving = false; sh.error = `Speichern fehlgeschlagen: ${errMsg(e)}`; this.render(); }
    }
    async removeAssign() {
      const sh = this.S.sheet;
      await this._hass.callApi('DELETE', `config/automation/config/${sh.autoId}`);
      if (sh.memory) { try { await this.ws({ type: 'input_text/delete', input_text_id: sh.memory.split('.')[1] }); } catch (e) { /* Helper war schon weg */ } }
      await sleep(600); await this.load(); this.S.sheet = null; this.render(); this.toast('Zuordnung gelöscht');
    }
    async resetPos() {
      const sh = this.S.sheet; if (!sh || !sh.memory) return;
      await this._hass.callService('input_text', 'set_value', { entity_id: sh.memory, value: '' });
      this.render(); this.toast('Gemerkte Position gelöscht');
    }
    async delTag(t) {
      if (!t) return;
      await this.ws({ type: 'tag/delete', tag_id: t.tag_id });
      await sleep(400); this.render(); this.toast('Karte entfernt');
    }
    async test(entity) {
      const a = this.S.autos[entity]; if (!a || !a.media || !a.scanners[0]) return;
      const m = a.media;
      await this._hass.callService('media_player', 'play_media', { entity_id: a.scanners[0].player, media: { media_content_id: m.media_content_id, media_content_type: m.media_content_type } });
      this.toast(`Spielt auf ${this._playerName(a.scanners[0].player)}`);
    }
    async addScanner(deviceId) {
      const S = this.S;
      S.settings.scanners.push({ reader: deviceId, player: '' });
      await this.saveSettings(); this.render(); this.toast('Hinzugefügt – jetzt Lautsprecher wählen');
    }
  }

  if (!customElements.get('nfc-karten-card')) customElements.define('nfc-karten-card', NfcKartenCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: 'nfc-karten-card', name: 'NFC-Karten-Manager', description: 'Musikkarten für die NFC-Musikbox verwalten' });
})();
