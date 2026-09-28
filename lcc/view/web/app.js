// Веб-представление (View): только отображение и передача ввода на сервер.
// Все проверки и права — на сервере (слой Model), здесь их нет.
const { createApp } = Vue;

const pad = n => String(n).padStart(2, "0");
const isoLocal = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
const isoDate = d => isoLocal(d).slice(0, 10);
const inDays = (days, hours = 0) => { const d = new Date(); d.setDate(d.getDate() + days); d.setHours(d.getHours() + hours, 0, 0, 0); return d; };

const TABS = [
  { id: "launches", title: "Пуски", perm: "view_schedule" },
  { id: "register", title: "Новый пуск", perm: "manage_launches" },
  { id: "fuel", title: "Топливо", perm: "manage_fuel" },
  { id: "weather", title: "Метео", perm: "record_weather" },
  { id: "telemetry", title: "Телеметрия", perm: "record_incident" },
  { id: "pads", title: "Площадки", perm: "view_schedule" },
  { id: "reports", title: "Отчёты", perm: "view_reports" },
];

createApp({
  data: () => ({
    token: sessionStorage.getItem("lcc_token"),
    auth: { login: "", password: "" },
    me: null, tab: "launches", toast: null,
    ref: { vehicle_types: [], pads: [], channels: [], postpone_reasons: [], fuel_components: [] },
    launches: [], card: null, batches: [], reportList: [], report: null,
    result: { pad_hours: 10 }, cancelReason: "",
    postponeForm: { reason: "weather", new_start: isoLocal(inDays(1, 1)), new_end: isoLocal(inDays(1, 2)), comment: "" },
    reg: { vehicle_type_id: null, pad_id: null, payload: "", target_orbit: "", window_start: isoLocal(inDays(7)), window_end: isoLocal(inDays(7, 1)) },
    refuelForm: { launch_id: null, batch_id: null, volume: 10 },
    batchForm: { batch_no: "", component: "fuel", grade: "РГ-1", volume: 100, produced_on: isoDate(inDays(-10)), expires_on: isoDate(inDays(180)) },
    wx: { launch_id: null, ground_wind: 5, altitude_wind: 15, temperature: 10, cloud_base: 1200, thunderstorm: false },
    wxResult: null,
    inc: { launch_id: null, channel_id: null, description: "Потеря сигнала", measures: "" },
    rep: { key: "launches", from: isoDate(inDays(-60)), to: isoDate(inDays(30)) },
  }),
  computed: {
    tabs() { return TABS.filter(t => this.can(t.perm)); },
    activeLaunches() { return this.launches.filter(l => !l.final); },
  },
  methods: {
    can(p) { return this.me && this.me.permissions.includes(p); },
    launchesIn(status) { return this.launches.filter(l => l.status === status); },
    notify(text, type = "ok") { this.toast = { text, type }; clearTimeout(this._t); this._t = setTimeout(() => this.toast = null, 6000); },

    async api(method, url, body) {
      const res = await fetch(url, {
        method, headers: { "Content-Type": "application/json", ...(this.token ? { Authorization: "Bearer " + this.token } : {}) },
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401 && this.me) { this.me = null; this.token = null; }
      if (!res.ok) throw new Error(data.error || res.statusText);
      return data;
    },
    async post(url, body = {}) {
      try {
        const data = await this.api("POST", url, body);
        this.notify(data.message || "Готово");
        await this.reload();
      } catch (e) { this.notify(e.message, "error"); }
    },

    async login() {
      try {
        const { token } = await this.api("POST", "/api/login", this.auth);
        this.token = token; sessionStorage.setItem("lcc_token", token);
        this.auth.password = "";
        await this.start();
      } catch (e) { this.notify(e.message, "error"); }
    },
    async logout() {
      await this.api("POST", "/api/logout").catch(() => {});
      sessionStorage.removeItem("lcc_token");
      Object.assign(this, { token: null, me: null, card: null, report: null });
    },
    async start() {
      this.me = await this.api("GET", "/api/me");
      this.tab = this.tabs[0].id;
      await this.reload();
    },
    async reload() {
      this.ref = await this.api("GET", "/api/reference");
      this.launches = await this.api("GET", "/api/launches");
      if (this.can("manage_fuel")) this.batches = await this.api("GET", "/api/fuel");
      if (this.can("view_reports")) this.reportList = await this.api("GET", "/api/reports");
      if (this.card) await this.openCard(this.card.id);
      // значения по умолчанию для выпадающих списков
      this.reg.vehicle_type_id ??= this.ref.vehicle_types[0]?.id;
      this.reg.pad_id ??= this.ref.pads[0]?.id;
      this.refuelForm.launch_id ??= this.launchesIn("fueling")[0]?.id;
      this.refuelForm.batch_id ??= this.batches[0]?.id;
      this.wx.launch_id ??= this.activeLaunches[0]?.id;
      this.inc.launch_id ??= this.launches.find(l => l.status !== "planned" && l.status !== "cancelled")?.id;
    },
    async openCard(id) {
      try { this.card = await this.api("GET", `/api/launches/${id}`); } catch (e) { this.notify(e.message, "error"); }
    },
    async register() {
      try {
        const data = await this.api("POST", "/api/launches", this.reg);
        this.notify(data.message);
        await this.reload(); this.tab = "launches"; await this.openCard(data.id);
      } catch (e) { this.notify(e.message, "error"); }
    },
    refuel() { this.post(`/api/launches/${this.refuelForm.launch_id}/refuel`, this.refuelForm); },
    async weather() {
      try {
        this.wxResult = await this.api("POST", `/api/launches/${this.wx.launch_id}/weather`, this.wx);
        await this.reload();
      } catch (e) { this.notify(e.message, "error"); }
    },
    async buildReport() {
      try { this.report = await this.api("GET", `/api/reports/${this.rep.key}?from=${this.rep.from}&to=${this.rep.to}`); }
      catch (e) { this.notify(e.message, "error"); }
    },
    async downloadCsv() {
      const res = await fetch(`/api/reports/${this.rep.key}.csv?from=${this.rep.from}&to=${this.rep.to}`,
                              { headers: { Authorization: "Bearer " + this.token } });
      if (!res.ok) return this.notify((await res.json()).error, "error");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await res.blob());
      a.download = `${this.report.title}.csv`;
      a.click();
      URL.revokeObjectURL(a.href);
    },
  },
  async mounted() {
    if (this.token) await this.start().catch(() => { this.token = null; sessionStorage.removeItem("lcc_token"); });
  },
}).mount("#app");
