/* NFC-Musikbox – Panel "Musikkarten"
 * Karten zuordnen (Medium, Betriebsart, Lesegeräte), gemerkte Stelle ansehen/zurücksetzen,
 * Lesegeräte anzeigen. Daten kommen per Websocket von der Integration nfc_musikbox.
 * Login, Rechte und Fernzugriff laufen über Home Assistant (Panel nur für Admins).
 */
(() => {
  const DOMAIN = 'nfc_musikbox';
  const FRESH_MS = 120000;
  const INTEGRATION_URL = `/config/integrations/integration/${DOMAIN}`;

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const errMsg = (e) => (e && (e.message || (e.error && e.error.message) || e.error)) || String(e);
  const ts = (iso) => { const t = Date.parse(iso || ''); return Number.isNaN(t) ? 0 : t; };
  const ago = (iso) => {
    const t = ts(iso);
    if (!t) return 'noch nie gescannt';
    const d = (Date.now() - t) / 1000;
    if (d < 45) return 'gerade eben';
    if (d < 3600) return `vor ${Math.max(1, Math.round(d / 60))} Min.`;
    if (d < 86400) return `vor ${Math.round(d / 3600)} Std.`;
    return new Date(t).toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit' });
  };
  const fmtPos = (sec) => { const t = Math.max(0, Math.round(sec)); const h = Math.floor(t / 3600); const m = Math.floor((t % 3600) / 60); const r = String(t % 60).padStart(2, '0'); return h ? `${h}:${String(m).padStart(2, '0')}:${r}` : `${m}:${r}`; };
  const posMain = (p) => `${p.q ? `Titel ${p.q} · ` : ''}${fmtPos(p.p)}`;
  const PLAYER_STATE = { playing: 'spielt', paused: 'pausiert', idle: 'bereit', buffering: 'lädt', off: 'aus', on: 'an', standby: 'Standby', unavailable: 'nicht erreichbar', unknown: 'unbekannt' };
  const volLabel = (v) => (Number(v) ? `${Number(v)} %` : 'unverändert');
  const REPEAT_LABEL = { off: 'Wiederholen aus', all: 'Wiederholen', one: 'Titel wiederholen' };
  const ic = (n, cls = '') => `<ha-icon class="${cls}" icon="mdi:${n}"></ha-icon>`;
  const NO_TAG = new Set(['none', 'unknown', 'unavailable', '']);

  const CSS = `
  :host{display:block;min-height:100vh;background:var(--primary-background-color);color:var(--primary-text-color);
    font-family:var(--ha-font-family-body,var(--paper-font-body1_-_font-family,Roboto,system-ui,sans-serif));
    --acc:var(--primary-color,#03a9f4);--bg2:var(--secondary-background-color,#f3f4f6);--card:var(--card-background-color,#fff);
    --line:var(--divider-color,rgba(127,127,127,.25));--mute:var(--secondary-text-color,#6b7280);--bad:var(--error-color,#db4437)}
  *{box-sizing:border-box}
  .top{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:4px;height:56px;padding:0 4px;
    background:var(--app-header-background-color,var(--primary-color));color:var(--app-header-text-color,#fff)}
  .top .t{flex:1;font-size:20px;font-weight:500;padding-left:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .top button{width:48px;height:48px;border-radius:50%;display:grid;place-items:center;--mdc-icon-size:24px}
  .wrap{max-width:760px;margin:0 auto;padding:16px 16px 96px}
  .sub{margin:0 4px 6px;color:var(--mute);font-size:14px}
  h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--mute);margin:26px 4px 10px;font-weight:600}
  button{font:inherit;color:inherit;cursor:pointer;border:0;background:none;padding:0}
  .tile:active,.btn:active{transform:scale(.98)}
  .btn{min-height:46px;padding:0 20px;border-radius:14px;font-weight:600;background:var(--bg2);display:inline-flex;align-items:center;justify-content:center;gap:8px;--mdc-icon-size:20px}
  .btn.primary{background:var(--acc);color:var(--text-primary-color,#fff)}
  .btn.danger{color:var(--bad)}
  .btn[disabled]{opacity:.45;pointer-events:none}
  .btn.small{min-height:38px;padding:0 14px;font-size:14px;border-radius:12px}
  .tile{display:flex;gap:14px;align-items:center;background:var(--card);border:1px solid var(--line);border-radius:18px;padding:12px;margin-bottom:10px;cursor:pointer;box-shadow:0 1px 2px rgba(0,0,0,.05);transition:transform .08s}
  .tile.off{opacity:.55}
  .art{flex:none;width:64px;height:64px;border-radius:12px;background:var(--bg2);display:grid;place-items:center;overflow:hidden;color:var(--mute);--mdc-icon-size:28px}
  .art img{width:100%;height:100%;object-fit:cover;display:block}
  .meta{flex:1;min-width:0}
  .title{font-weight:650;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .media{color:var(--mute);font-size:14px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
  .chip{display:inline-flex;align-items:center;gap:4px;font-size:12px;padding:3px 9px;border-radius:99px;background:var(--bg2);color:var(--mute);--mdc-icon-size:14px}
  .chip.on{background:color-mix(in srgb,var(--acc) 15%,var(--card));color:var(--acc)}
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
  .overlay.top2{z-index:30}
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
  input[type=text]{width:100%;min-height:48px;padding:0 14px;border-radius:14px;border:1px solid var(--line);background:var(--bg2);color:var(--primary-text-color);font:inherit;font-size:16px}
  input:focus{outline:2px solid var(--acc);outline-offset:-1px}
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
  .g .gi{aspect-ratio:1;background:var(--bg2);display:grid;place-items:center;--mdc-icon-size:34px;color:var(--mute);width:100%}
  .g img{width:100%;height:100%;object-fit:cover;display:block}
  .g .gt{margin:8px 10px 6px;font-size:13px;font-weight:600;line-height:1.25;min-height:2.5em;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
  .g .ga{padding:0 8px 8px;display:flex;flex-wrap:wrap;gap:6px;margin-top:auto}
  .g .ga .btn{flex:1 1 auto;min-width:0;min-height:34px;padding:0 8px;font-size:13px;border-radius:10px}
  .media ha-icon{--mdc-icon-size:16px;vertical-align:-3px}
  .row{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px;margin-bottom:10px}
  .row .head{display:flex;align-items:center;gap:10px;--mdc-icon-size:22px}
  .row .head b{flex:1;min-width:0}
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
  .seg.compact button{padding:10px 6px;text-align:center;font-size:13px}
  .vol{display:flex;align-items:center;gap:10px;--mdc-icon-size:22px;color:var(--mute)}
  .vol input{flex:1;accent-color:var(--acc);min-width:0}
  .vol .vv{min-width:84px;text-align:right;font-size:14px;color:var(--primary-text-color)}
  .posbox{display:flex;align-items:center;gap:10px;padding:12px 14px;border-radius:14px;background:var(--bg2);--mdc-icon-size:22px}
  .posbox .t{flex:1;min-width:0}.posbox b{display:block}.posbox small{display:block;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .tests{display:flex;gap:8px;flex-wrap:wrap}
  .toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);background:#222;color:#fff;padding:12px 18px;border-radius:12px;z-index:40;font-size:14px;max-width:90vw;box-shadow:0 6px 20px rgba(0,0,0,.3)}
  .toast.err{background:var(--bad)}
  .spin{display:grid;place-items:center;padding:40px;color:var(--mute)}
  `;

  class NfcMusikboxPanel extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: 'open' });
      this.S = { view: 'cards', data: null, error: null, sheet: null, picker: null, confirm: null, toast: null };
      this._sig = '';
      this._thumbs = new Map();
      this._pendingSign = new Set();
      this.narrow = false;
    }

    /* ---------- Lebenszyklus ---------- */
    set hass(h) {
      const first = !this._hass;
      this._hass = h;
      if (first) { this._boot(); return; }
      const sig = this._sigStr();
      if (sig !== this._sig) {
        this._sig = sig;
        if (!this.S.sheet && !this.S.picker) this.render();
      }
    }
    get hass() { return this._hass; }

    connectedCallback() {
      this._timer = setInterval(() => { if (!this.S.sheet && !this.S.picker) this.render(); }, 30000);
      if (this._hass && !this._unsub) this._subscribe();
    }
    disconnectedCallback() {
      clearInterval(this._timer);
      if (this._unsub) { this._unsub.then((u) => u()).catch(() => {}); this._unsub = null; }
    }

    _boot() {
      this.shadowRoot.addEventListener('click', (e) => this._click(e));
      this.shadowRoot.addEventListener('input', (e) => this._input(e));
      this.shadowRoot.addEventListener('change', (e) => this._change(e));
      this.render();
      this._subscribe();
    }

    _subscribe() {
      this._unsub = this._hass.connection.subscribeMessage((data) => {
        this.S.data = data;
        this.S.error = null;
        if (this.S.sheet) this._refreshSheet();
        this.render();
      }, { type: `${DOMAIN}/subscribe` });
      this._unsub.catch((e) => { this.S.error = errMsg(e); this.render(); });
    }

    ws(msg) { return this._hass.callWS(msg); }

    /* ---------- Daten ---------- */
    get readers() { return (this.S.data && this.S.data.readers) || []; }
    get cards() { return (this.S.data && this.S.data.cards) || []; }

    _tagEntities() {
      const out = new Map();
      Object.values(this._hass.states).forEach((s) => {
        if (s.entity_id.startsWith('tag.') && s.attributes && s.attributes.tag_id) {
          out.set(String(s.attributes.tag_id).toUpperCase(), {
            entity: s.entity_id, tag_id: s.attributes.tag_id,
            name: s.attributes.friendly_name, last: NO_TAG.has(s.state) ? null : s.state,
          });
        }
      });
      return out;
    }
    _onReader() {
      const m = new Map();
      this.readers.forEach((r) => {
        const st = r.card_sensor && this._hass.states[r.card_sensor];
        if (st && !NO_TAG.has(st.state)) m.set(st.state, r);
      });
      return m;
    }
    _unassigned() {
      const assigned = new Set(this.cards.map((c) => c.tag_id));
      const tags = this._tagEntities();
      const list = new Map();
      ((this.S.data && this.S.data.seen) || []).forEach((s) => list.set(s.tag_id, { tag_id: s.tag_id, last: s.last }));
      tags.forEach((t, id) => {
        const prev = list.get(id);
        const last = ts(t.last) > ts(prev && prev.last) ? t.last : prev && prev.last;
        list.set(id, { tag_id: id, last });
      });
      return [...list.values()].filter((t) => !assigned.has(t.tag_id)).map((t) => {
        const te = tags.get(t.tag_id);
        const name = te && te.name && !/^Tag /.test(te.name) ? te.name : t.tag_id;
        return { ...t, name, entity: te ? te.entity : null, haTagId: te ? te.tag_id : null };
      }).sort((a, b) => ts(b.last) - ts(a.last));
    }
    _sigStr() {
      const tags = [...this._tagEntities().values()].map((t) => `${t.tag_id}|${t.name}|${t.last}`).join(';');
      const rd = this.readers.map((r) => {
        const st = r.card_sensor && this._hass.states[r.card_sensor];
        const pl = this._hass.states[r.player];
        return `${st ? st.state : ''}|${pl ? pl.state : ''}`;
      }).join(';');
      return `${tags}#${rd}`;
    }
    _readerName(id) { const r = this.readers.find((x) => x.id === id); return r ? r.title : 'Unbekanntes Lesegerät'; }
    _playerShort(r) { return String(r.player_name || r.player).replace(/^Sonos\s+/i, ''); }
    _lastFor(tagId) {
      const seen = ((this.S.data && this.S.data.seen) || []).find((s) => s.tag_id === tagId);
      const te = this._tagEntities().get(tagId);
      const a = seen && seen.last; const b = te && te.last;
      return ts(a) >= ts(b) ? a : b;
    }

    toast(msg, err = false) {
      this.S.toast = { msg, err };
      this.render();
      clearTimeout(this._tt);
      this._tt = setTimeout(() => { this.S.toast = null; this.render(); }, err ? 6000 : 2200);
    }

    /* ---------- Cover ---------- */
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
      if (S.error) body = `<div class="wrap"><div class="warn"><b>Fehler</b><br>${esc(S.error)}</div><button class="btn" data-a="reload">Erneut versuchen</button></div>`;
      else if (!S.data) body = `<div class="wrap"><div class="spin">Lade Karten …</div></div>`;
      else if (!S.data.loaded) body = `<div class="wrap"><div class="warn">Die Integration NFC-Musikbox ist nicht geladen.</div><button class="btn" data-a="nav" data-u="${INTEGRATION_URL}">Zur Integration</button></div>`;
      else body = S.view === 'settings' ? this.vSettings() : this.vCards();
      const title = S.view === 'settings' ? 'Lesegeräte' : 'Musikkarten';
      const left = S.view === 'settings'
        ? `<button data-a="back" aria-label="Zurück">${ic('arrow-left')}</button>`
        : `<button data-a="menu" aria-label="Menü">${ic('menu')}</button>`;
      const right = S.view === 'settings' ? '' : `<button data-a="settings" aria-label="Lesegeräte">${ic('cog-outline')}</button>`;
      const root = this.shadowRoot;
      const keep = root.querySelector('.sh-body');
      const scroll = keep ? keep.scrollTop : 0;
      root.innerHTML = `<style>${CSS}</style><div class="top">${left}<div class="t">${title}</div>${right}</div>${body}${this.vSheet()}${this.vPicker()}${this.vConfirm()}${S.toast ? `<div class="toast ${S.toast.err ? 'err' : ''}">${esc(S.toast.msg)}</div>` : ''}`;
      if (scroll) { const nb = root.querySelector('.sh-body'); if (nb) nb.scrollTop = scroll; }
    }

    vCards() {
      const cards = [...this.cards].sort((a, b) => a.name.localeCompare(b.name, 'de'));
      const fresh = this._unassigned();
      const onReader = this._onReader();
      const nR = this.readers.length;
      let h = `<div class="wrap"><p class="sub">${cards.length} ${cards.length === 1 ? 'Karte' : 'Karten'} · ${nR} ${nR === 1 ? 'Lesegerät' : 'Lesegeräte'}</p>`;
      if (!nR) h += `<div class="warn"><b>Noch kein Lesegerät.</b> Füge es unter <i>Lesegeräte</i> (Zahnrad oben rechts) hinzu.</div>`;
      if (fresh.length) {
        h += `<h2>Noch ohne Musik</h2>`;
        fresh.forEach((t) => {
          const lying = onReader.has(t.tag_id);
          const isFresh = lying || (t.last && Date.now() - ts(t.last) < FRESH_MS);
          h += `<div class="tile new ${isFresh ? 'fresh' : ''}" data-a="new" data-t="${esc(t.tag_id)}">
            <div class="art">${ic('nfc-variant')}</div>
            <div class="meta">${lying ? '<div class="badge">Liegt auf</div>' : isFresh ? '<div class="badge">Gerade gescannt</div>' : ''}<div class="title">${esc(t.name)}</div>
              <div class="media">${esc(t.name !== t.tag_id ? `${t.tag_id} · ` : '')}${esc(ago(t.last))}</div>
              <div style="margin-top:10px"><button class="btn small primary" data-a="new" data-t="${esc(t.tag_id)}">Zuordnen</button></div></div>
            <button class="x" data-a="del-tag" data-t="${esc(t.tag_id)}" aria-label="Karte entfernen" style="align-self:flex-start">${ic('close')}</button></div>`;
        });
      }
      if (cards.length || fresh.length) h += `<h2>Zugeordnete Karten</h2>`;
      if (!cards.length && !fresh.length) {
        h += `<div class="empty">${ic('nfc-variant')}<b>Noch keine Karte</b>Lege eine Karte auf das Lesegerät.<br>Sie erscheint hier automatisch – dann wählst du ihre Musik aus.</div>`;
      } else if (!cards.length) {
        h += `<div class="empty"><b>Noch nichts zugeordnet</b>Tippe oben bei einer Karte auf „Zuordnen“.</div>`;
      }
      cards.forEach((c) => {
        const m = c.media || {};
        const title = (m.metadata && m.metadata.title) || m.media_content_id || 'Kein Medium';
        const readers = c.readers.length ? this.readers.filter((r) => c.readers.includes(r.id)) : this.readers;
        const chips = readers.map((r) => `<span class="chip">${ic('speaker')}${esc(this._playerShort(r))}</span>`).join('');
        const lying = onReader.has(c.tag_id);
        const pos = c.mode === 'tonie' && c.position ? `${posMain(c.position)}${c.position.t ? ` · ${c.position.t}` : ''}` : null;
        h += `<div class="tile ${c.enabled ? '' : 'off'}" data-a="edit" data-t="${esc(c.tag_id)}">
          ${this.art(m.metadata && m.metadata.thumbnail)}
          <div class="meta"><div class="title">${esc(c.name)}</div><div class="media">${esc(title)}</div>
            <div class="chips">${lying ? `<span class="chip on">${ic('nfc-variant')}liegt auf</span>` : ''}${chips}<span class="chip">${ic('clock-outline')}${esc(ago(this._lastFor(c.tag_id)))}</span>${c.enabled ? '' : `<span class="chip">${ic('power-off')}deaktiviert</span>`}${c.mode === 'simple' ? `<span class="chip">${ic('music')}Musik${c.shuffle ? ' · Zufall' : ''}${c.repeat && c.repeat !== 'off' ? ` · ${REPEAT_LABEL[c.repeat]}` : ''}</span>` : ''}</div>
            ${pos ? `<div class="media" style="margin-top:6px">${ic('bookmark-outline')} ${esc(pos)}</div>` : ''}</div>
          <button class="play" data-a="test" data-t="${esc(c.tag_id)}" aria-label="Zum Testen von vorne abspielen">${ic('play')}</button></div>`;
      });
      return h + `</div>`;
    }

    vSettings() {
      let h = `<div class="wrap"><p class="sub">Jedes Lesegerät gehört zu genau einem Lautsprecher.</p><h2>Lesegeräte</h2>`;
      if (!this.readers.length) h += `<div class="empty"><b>Noch kein Lesegerät</b>Füge dein ESPHome-Lesegerät in der Integration hinzu.</div>`;
      const onTag = (r) => { const st = r.card_sensor && this._hass.states[r.card_sensor]; return st ? st.state : null; };
      this.readers.forEach((r) => {
        const tag = onTag(r);
        const card = tag && this.cards.find((c) => c.tag_id === tag);
        const pl = this._hass.states[r.player];
        h += `<div class="row"><div class="head">${ic('nfc-variant')}<b>${esc(r.title)}</b></div>
          <div class="chips"><span class="chip">${ic('speaker')}${esc(r.player_name)}${pl ? ` · ${esc(PLAYER_STATE[pl.state] || pl.state)}` : ''}</span>
            <span class="chip ${r.supports_restore ? 'on' : ''}">${ic(r.supports_restore ? 'bookmark-check-outline' : 'restart')}${r.supports_restore ? 'Fortsetzen an der Stelle' : 'nur von vorne'}</span>
            ${tag && !NO_TAG.has(tag) ? `<span class="chip on">${ic('cards-outline')}${esc(card ? card.name : tag)}</span>` : ''}</div>
          <label class="f">Startlautstärke</label>
          <div class="vol">${ic('volume-medium')}<input type="range" min="0" max="100" step="5" value="${Number(r.start_volume) || 0}" data-vol="${esc(r.id)}" aria-label="Startlautstärke">
            <span class="vv" data-vv="${esc(r.id)}">${volLabel(r.start_volume)}</span></div>
          ${r.ready ? '' : `<p class="hint" style="color:var(--bad)">Sensor „Karte auf dem Reader“ fehlt. Läuft auf dem Lesegerät die Firmware v3?</p>`}</div>`;
      });
      h += `<div class="tests" style="margin-top:14px"><button class="btn primary" data-a="nav" data-u="${INTEGRATION_URL}">${ic('plus')}Lesegerät hinzufügen</button>
        <button class="btn" data-a="nav" data-u="${INTEGRATION_URL}">${ic('pencil-outline')}Lautsprecher ändern</button></div>
        <p class="hint">Lesegeräte, Lautsprecher und Zeiten (Optionen) verwaltest du auf der Seite der Integration.</p>`;
      return h + `</div>`;
    }

    vSheet() {
      const sh = this.S.sheet;
      if (!sh) return '';
      const m = sh.media;
      const title = m ? ((m.metadata && m.metadata.title) || m.media_content_id) : null;
      const card = sh.edit ? this.cards.find((c) => c.tag_id === sh.tag_id) : null;
      const pos = card && card.position;
      const ready = this.readers.filter((r) => r.ready);
      return `<div class="overlay" data-a="close-sheet"><div class="sheet" data-stop="1">
        <div class="sh-head"><h3>${sh.edit ? 'Karte bearbeiten' : 'Neue Karte'}</h3><button class="x" data-a="close-sheet" aria-label="Schließen">${ic('close')}</button></div>
        <div class="sh-body">
          <label class="f" style="margin-top:4px">Name</label>
          <input type="text" data-in="name" value="${esc(sh.name)}" placeholder="z. B. Hörspiel Puderzucker" autocomplete="off">
          <p class="hint">Karte ${esc(sh.tag_id)}</p>
          <label class="f">Medium</label>
          <button class="pick" data-a="pick-media">${this.art(m && m.metadata && m.metadata.thumbnail)}
            <span class="meta"><span class="title" style="display:block">${esc(title || 'Medium auswählen')}</span>
            <span class="media" style="display:block">${m ? 'Tippen zum Ändern' : 'Album, Playlist, Favorit, Radio …'}</span></span>${ic('chevron-right')}</button>
          <label class="f">Betriebsart</label>
          <div class="seg">
            <button class="${sh.mode === 'tonie' ? 'on' : ''}" data-a="set-mode" data-m="tonie">Hörspiel<small>Läuft nur, solange die Karte liegt. Stelle wird gemerkt.</small></button>
            <button class="${sh.mode === 'simple' ? 'on' : ''}" data-a="set-mode" data-m="simple">Musik<small>Karte startet die Wiedergabe, Abziehen tut nichts.</small></button>
          </div>
          ${sh.mode === 'simple' ? `<label class="f">Zufallswiedergabe</label><div class="seg compact">
            ${[[null, 'Unverändert'], [true, 'An'], [false, 'Aus']].map(([v, l]) => `<button class="${sh.shuffle === v ? 'on' : ''}" data-a="set-shuffle" data-v="${v}">${l}</button>`).join('')}</div>
            <label class="f">Wiederholen</label><div class="seg compact">
            ${[[null, 'Unverändert'], ['off', 'Aus'], ['all', 'Alle'], ['one', 'Titel']].map(([v, l]) => `<button class="${sh.repeat === v ? 'on' : ''}" data-a="set-repeat" data-v="${v}">${l}</button>`).join('')}</div>
            <p class="hint">„Unverändert“ lässt die Einstellung des Lautsprechers, wie sie ist.</p>` : `<p class="hint">Zufallswiedergabe wird beim Start ausgeschaltet, damit die gemerkte Stelle stimmt.</p>`}
          ${sh.mode === 'tonie' && sh.edit ? `<label class="f">Gemerkte Stelle</label><div class="posbox">${ic('bookmark-outline')}
            <span class="t">${pos ? `<b>${esc(posMain(pos))}</b><small>${esc(pos.t || 'Titel unbekannt')}</small>` : '<b>Noch keine Stelle</b><small>Wird beim Abziehen der Karte gemerkt.</small>'}</span>
            ${pos ? '<button class="btn small" data-a="reset-pos">Zurücksetzen</button>' : ''}</div>` : ''}
          <label class="f">Funktioniert an</label>
          ${this.readers.length ? this.readers.map((r) => `<button class="sc ${sh.sel.has(r.id) ? 'on' : ''} ${r.ready ? '' : 'off'}" data-a="toggle-reader" data-r="${esc(r.id)}">
            ${ic(sh.sel.has(r.id) ? 'checkbox-marked-circle' : 'checkbox-blank-circle-outline')}
            <span class="t">${esc(r.title)}<small>${r.ready ? `→ ${esc(r.player_name)}` : 'Sensor „Karte auf dem Reader“ fehlt'}</small></span></button>`).join('')
          : `<p class="hint">Noch kein Lesegerät eingerichtet. <button class="btn small" data-a="settings">Zu den Lesegeräten</button></p>`}
          ${sh.edit ? `<label class="f">Karte</label><button class="swrow" data-a="toggle-enabled"><span class="t">Karte aktiv<small>${sh.enabled ? 'Reagiert auf das Auflegen.' : 'Deaktiviert: Auflegen bewirkt nichts.'}</small></span><span class="sw ${sh.enabled ? 'on' : ''}"></span></button>
            <label class="f">Testen</label><div class="tests"><button class="btn small" data-a="test" data-t="${esc(sh.tag_id)}">${ic('play')}Von vorne</button>
            ${sh.mode === 'tonie' ? `<button class="btn small" data-a="test-resume" data-t="${esc(sh.tag_id)}">${ic('bookmark-outline')}Fortsetzen</button>` : ''}</div>
            <p class="hint">Spielt auf dem Lautsprecher des ersten passenden Lesegeräts, ohne dass die Karte aufliegt.</p>` : ''}
          ${sh.error ? `<div class="warn" style="margin-top:12px">${esc(sh.error)}</div>` : ''}
        </div>
        <div class="sh-foot">
          ${sh.edit ? `<button class="btn danger" data-a="remove-assign" style="flex:0 0 auto" aria-label="Zuordnung löschen">${ic('delete-outline')}</button>` : ''}
          <button class="btn primary" data-a="save" ${sh.saving || !ready.length ? 'disabled' : ''}>${sh.saving ? 'Speichere …' : 'Speichern'}</button>
        </div></div></div>`;
    }

    vPicker() {
      const p = this.S.picker;
      if (!p) return '';
      const q = (p.filter || '').toLowerCase();
      const items = (p.items || []).filter((i) => !q || (i.title || '').toLowerCase().includes(q));
      const crumbs = [`<button data-a="crumb" data-i="-1">Start</button>`].concat(p.stack.map((s, i) => (i === p.stack.length - 1
        ? `<span>${esc(s.title)}</span>` : `<button data-a="crumb" data-i="${i}">${esc(s.title)}</button>`)));
      let list;
      if (p.loading) list = `<div class="spin">Lade …</div>`;
      else if (p.error) list = `<div class="warn">${esc(p.error)}</div>`;
      else if (!items.length) list = `<div class="empty"><b>Nichts gefunden</b></div>`;
      else list = `<div class="grid">${items.map((i) => {
        const idx = p.items.indexOf(i);
        const folder = i.can_expand;
        const t = this.thumb(i.thumbnail);
        return `<div class="g"><button class="gi" data-a="${folder ? 'open' : 'choose'}" data-i="${idx}">
          ${t ? `<img src="${esc(t)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ic(folder ? 'folder-music-outline' : 'music-note')}</button>
          <div class="gt">${esc(i.title)}</div>
          <div class="ga">${folder ? `<button class="btn small" data-a="open" data-i="${idx}">Öffnen</button>` : ''}${i.can_play ? `<button class="btn small primary" data-a="choose" data-i="${idx}">Wählen</button>` : ''}</div></div>`;
      }).join('')}</div>`;
      return `<div class="overlay top2"><div class="sheet full" data-stop="1">
        <div class="sh-head"><button class="x" data-a="close-picker" aria-label="Zurück">${ic('arrow-left')}</button><h3>Medium wählen</h3></div>
        <div class="sh-body"><div class="crumbs">${crumbs.join('<span>›</span>')}</div>
          <input type="text" data-in="filter" placeholder="Filtern …" value="${esc(p.filter || '')}" style="margin-bottom:12px">${list}</div></div></div>`;
    }

    vConfirm() {
      const c = this.S.confirm;
      if (!c) return '';
      return `<div class="overlay top2" data-a="confirm-no"><div class="sheet" data-stop="1" style="border-radius:24px;margin:16px;width:auto">
        <div class="sh-head"><h3>${esc(c.title)}</h3></div><div class="sh-body"><p class="hint" style="font-size:15px">${esc(c.text)}</p></div>
        <div class="sh-foot"><button class="btn" data-a="confirm-no">Abbrechen</button><button class="btn primary" data-a="confirm-yes">${esc(c.ok || 'OK')}</button></div></div></div>`;
    }

    /* ---------- Ereignisse ---------- */
    async _change(e) {
      const reader = e.target.dataset && e.target.dataset.vol;
      if (!reader) return;
      const value = Number(e.target.value);
      const r = this.readers.find((x) => x.id === reader);
      if (r) r.start_volume = value;
      try {
        await this.ws({ type: `${DOMAIN}/reader/update`, reader, start_volume: value });
        this.toast(`Startlautstärke: ${volLabel(value)}`);
      } catch (err) { this.toast(errMsg(err), true); }
    }

    _input(e) {
      const vr = e.target.dataset && e.target.dataset.vol;
      if (vr) {
        const lab = [...this.shadowRoot.querySelectorAll('[data-vv]')].find((x) => x.dataset.vv === vr);
        if (lab) lab.textContent = volLabel(e.target.value);
        return;
      }
      const k = e.target.dataset && e.target.dataset.in;
      if (k === 'name' && this.S.sheet) this.S.sheet.name = e.target.value;
      if (k === 'filter' && this.S.picker) {
        this.S.picker.filter = e.target.value;
        const pos = e.target.selectionStart;
        this.render();
        const f = this.shadowRoot.querySelector('[data-in=filter]');
        if (f) { f.focus(); try { f.setSelectionRange(pos, pos); } catch (_) { /* egal */ } }
      }
    }

    async _click(e) {
      const el = e.target.closest('[data-a]');
      if (!el) return;
      const a = el.dataset.a;
      // Klick im Sheet schließt das Overlay nicht
      if (el.classList.contains('overlay') && e.target !== el) return;
      e.stopPropagation();
      const S = this.S;
      try {
        switch (a) {
          case 'menu': this.dispatchEvent(new Event('hass-toggle-menu', { bubbles: true, composed: true })); break;
          case 'reload': S.error = null; S.data = null; this.render(); this._subscribe(); break;
          case 'settings': S.sheet = null; S.view = 'settings'; this.render(); break;
          case 'back': S.view = 'cards'; this.render(); break;
          case 'nav': history.pushState(null, '', el.dataset.u); window.dispatchEvent(new CustomEvent('location-changed')); break;
          case 'new': this.openSheet(el.dataset.t, false); break;
          case 'edit': this.openSheet(el.dataset.t, true); break;
          case 'close-sheet': S.sheet = null; this.render(); break;
          case 'toggle-reader': {
            const r = this.readers.find((x) => x.id === el.dataset.r);
            if (!r || !r.ready) { this.toast('Am Lesegerät fehlt der Karten-Sensor', true); break; }
            const s = S.sheet.sel; if (s.has(r.id)) s.delete(r.id); else s.add(r.id); this.render(); break;
          }
          case 'pick-media': await this.openPicker(); break;
          case 'close-picker': S.picker = null; this.render(); break;
          case 'crumb': await this.crumb(Number(el.dataset.i)); break;
          case 'open': await this.openItem(Number(el.dataset.i)); break;
          case 'choose': this.chooseItem(Number(el.dataset.i)); break;
          case 'save': await this.save(); break;
          case 'set-mode': S.sheet.mode = el.dataset.m; this.render(); break;
          case 'set-shuffle': S.sheet.shuffle = JSON.parse(el.dataset.v); this.render(); break;
          case 'set-repeat': S.sheet.repeat = el.dataset.v === 'null' ? null : el.dataset.v; this.render(); break;
          case 'toggle-enabled': S.sheet.enabled = !S.sheet.enabled; this.render(); break;
          case 'reset-pos':
            await this.ws({ type: `${DOMAIN}/position/reset`, tag_id: S.sheet.tag_id });
            this.toast('Gemerkte Stelle gelöscht'); break;
          case 'remove-assign':
            S.confirm = { title: 'Zuordnung löschen?', text: 'Die Karte spielt danach keine Musik mehr, bis du ihr wieder ein Medium gibst. Die gemerkte Stelle geht verloren.', ok: 'Löschen', run: () => this.removeAssign() };
            this.render(); break;
          case 'del-tag': {
            const t = this._unassigned().find((x) => x.tag_id === el.dataset.t);
            S.confirm = { title: 'Karte entfernen?', text: `„${t ? t.name : ''}“ verschwindet aus dieser Liste${t && t.entity ? ' und aus den Tags von Home Assistant' : ''}. Beim nächsten Scannen taucht sie wieder auf.`, ok: 'Entfernen', run: () => this.delTag(t) };
            this.render(); break;
          }
          case 'confirm-no': S.confirm = null; this.render(); break;
          case 'confirm-yes': { const c = S.confirm; S.confirm = null; this.render(); if (c && c.run) await c.run(); break; }
          case 'test': await this.test(el.dataset.t, false); break;
          case 'test-resume': await this.test(el.dataset.t, true); break;
          default: break;
        }
      } catch (err) { this.toast(errMsg(err), true); }
    }

    openSheet(tagId, edit) {
      const card = this.cards.find((c) => c.tag_id === tagId);
      const t = this._unassigned().find((x) => x.tag_id === tagId);
      const ready = this.readers.filter((r) => r.ready).map((r) => r.id);
      const sel = card && card.readers.length ? card.readers : ready;
      this.S.sheet = {
        edit: edit && !!card, tag_id: tagId, name: card ? card.name : (t && t.name !== tagId ? t.name : ''),
        media: card ? card.media : null, mode: card ? card.mode : 'tonie', enabled: card ? card.enabled : true,
        shuffle: card && typeof card.shuffle === 'boolean' ? card.shuffle : null, repeat: card ? card.repeat || null : null,
        sel: new Set(sel), error: null, saving: false,
      };
      this.render();
    }
    _refreshSheet() {
      const sh = this.S.sheet;
      if (sh.edit && !this.cards.some((c) => c.tag_id === sh.tag_id)) this.S.sheet = null;
    }

    /* ---------- Medien-Browser ---------- */
    _browsePlayer() {
      const sh = this.S.sheet;
      const r = this.readers.find((x) => sh && sh.sel.has(x.id)) || this.readers[0];
      return r ? r.player : null;
    }
    async openPicker() {
      const player = this._browsePlayer();
      if (!player) { this.toast('Erst ein Lesegerät mit Lautsprecher einrichten', true); return; }
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
      const metadata = {
        title: it.title, thumbnail: it.thumbnail || null, media_class: it.media_class || null,
        children_media_class: it.children_media_class || null,
        navigateIds: [{}].concat(p.stack.map((s) => ({ media_content_type: s.type, media_content_id: s.id }))),
      };
      this.S.sheet.media = { entity_id: p.player, media_content_id: it.media_content_id, media_content_type: it.media_content_type, metadata };
      this.S.picker = null;
      if (!this.S.sheet.name.trim()) this.S.sheet.name = it.title;
      this.render();
    }

    /* ---------- Aktionen ---------- */
    async save() {
      const sh = this.S.sheet;
      sh.name = (sh.name || '').trim();
      if (!sh.name) { sh.error = 'Bitte einen Namen eingeben.'; this.render(); return; }
      if (!sh.media) { sh.error = 'Bitte ein Medium auswählen.'; this.render(); return; }
      const ready = this.readers.filter((r) => r.ready).map((r) => r.id);
      const sel = ready.filter((id) => sh.sel.has(id));
      if (!sel.length) { sh.error = 'Bitte mindestens ein Lesegerät auswählen.'; this.render(); return; }
      sh.saving = true; sh.error = null; this.render();
      try {
        // Alle ausgewählt = an allen Lesegeräten, auch künftigen
        const readers = sel.length === ready.length ? [] : sel;
        await this.ws({ type: `${DOMAIN}/card/save`, tag_id: sh.tag_id, name: sh.name, media: sh.media, mode: sh.mode, enabled: sh.enabled, readers, shuffle: sh.shuffle, repeat: sh.repeat });
        await this._syncTagName(sh.tag_id, sh.name);
        this.S.sheet = null;
        this.render();
        this.toast('Gespeichert');
      } catch (e) { sh.saving = false; sh.error = `Speichern fehlgeschlagen: ${errMsg(e)}`; this.render(); }
    }
    async _syncTagName(tagId, name) {
      // Namen in die Tag-Verwaltung von Home Assistant spiegeln, falls der Tag dort existiert
      const te = this._tagEntities().get(tagId);
      if (!te || te.name === name) return;
      try { await this.ws({ type: 'tag/update', tag_id: te.tag_id, name }); } catch (e) { /* nicht kritisch */ }
    }
    async removeAssign() {
      const sh = this.S.sheet;
      await this.ws({ type: `${DOMAIN}/card/delete`, tag_id: sh.tag_id });
      this.S.sheet = null; this.render(); this.toast('Zuordnung gelöscht');
    }
    async delTag(t) {
      if (!t) return;
      await this.ws({ type: `${DOMAIN}/seen/delete`, tag_id: t.tag_id });
      if (t.haTagId) { try { await this.ws({ type: 'tag/delete', tag_id: t.haTagId }); } catch (e) { /* schon weg */ } }
      this.render(); this.toast('Karte entfernt');
    }
    async test(tagId, resume) {
      const r = await this.ws({ type: `${DOMAIN}/card/play`, tag_id: tagId, resume });
      const reader = this.readers.find((x) => x.id === r.reader);
      this.toast(`${resume ? 'Setzt fort' : 'Spielt'} auf ${reader ? reader.player_name : 'dem Lautsprecher'}`);
    }
  }

  if (!customElements.get('nfc-musikbox-panel')) customElements.define('nfc-musikbox-panel', NfcMusikboxPanel);
})();
