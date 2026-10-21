// Слой View: веб-интерфейс. Только отображение и передача ввода на сервер.
// Все проверки и права — на сервере (слой Model), здесь их нет.
// Страница запрашивает только то, что разрешено роли: иначе в аудите инспектора были бы лишние отказы.
const { createApp } = Vue;

const pad = n => String(n).padStart(2, "0");
const isoLocal = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
const isoDate = d => isoLocal(d).slice(0, 10);
const inDays = (days, hours = 0) => { const d = new Date(); d.setDate(d.getDate() + days); d.setHours(d.getHours() + hours, 0, 0, 0); return d; };
const GLOBAL_ROLES = ["head", "safety_inspector"];

const TABS = [
  { id: "launches", title: "Пуски", perm: ["view_schedule"] },
  { id: "register", title: "Новый пуск", perm: ["manage_launches"] },
  { id: "fuel", title: "Топливо", perm: ["manage_fuel"] },
  { id: "weather", title: "Метео", perm: ["record_weather", "request_forecast"] },
  { id: "telemetry", title: "Телеметрия", perm: ["record_incident"] },
  { id: "pads", title: "Площадки", perm: ["view_schedule"] },
  { id: "journals", title: "Журналы", perm: ["view_journals"] },
  { id: "staff", title: "Персонал", perm: ["manage_staff"] },
  { id: "audit", title: "Аудит", perm: ["view_audit"] },
  { id: "reports", title: "Отчёты", perm: ["view_reports", "view_incident_reports"] },
];

createApp({
  data: () => ({
    token: sessionStorage.getItem("lcc_token"),
    auth: { login: "", password: "" },
    me: null, tab: "launches", toast: null, spaceportFilter: "",
    search: { vehicle_type: "", status: "" },   // ЛР5: поиск пусков выполняет сервер (SQL)
    ref: { spaceports: [], vehicle_types: [], pads: [], channels: [], postpone_reasons: [], fuel_components: [] },
    launches: [], card: null, batches: [], reportList: [], report: null, staff: [], forecasts: [],
    maint: { kind: "inspection", notes: "" },
    spForm: { code: "", name: "", region: "" },
    padForm: { code: "", name: "", max_concurrent: 1, cycle_limit: 5, hours_limit: 250, spaceport_id: null },
    vtForm: { name: "", vehicle_class: "medium", stages_count: 2 },
    chForm: { channel_no: "", vehicle_system: "", parameter: "", frequency_hz: 10 },
    staffForm: { login: "", full_name: "", role: "pad_engineer", password: "", spaceport_id: null },
    result: { pad_hours: 10 }, cancelReason: "",
    postponeForm: { reason: "weather", new_start: isoLocal(inDays(1, 1)), new_end: isoLocal(inDays(1, 2)), comment: "" },
    reg: { vehicle_type_id: null, pad_id: null, payload: "", target_orbit: "", window_start: isoLocal(inDays(7)), window_end: isoLocal(inDays(7, 1)) },
    refuelForm: { launch_id: null, batch_id: null, mass_kg: 1000 },
    batchForm: { batch_no: "", component: "fuel", grade: "РГ-1", mass_kg: 100000, produced_on: isoDate(inDays(-10)), expires_on: isoDate(inDays(180)), spaceport_id: null },
    wx: { launch_id: null, ground_wind: 5, altitude_wind: 15, temperature: 10, cloud_base: 1200, thunderstorm: false },
    wxResult: null,
    fc: { launch_id: null, ground_wind: 5, altitude_wind: 15, temperature: 5, cloud_base: 1000, thunderstorm: false },
    inc: { launch_id: null, channel_id: null, description: "Потеря сигнала", measures: "" },
    rep: { key: "launches", from: isoDate(inDays(-60)), to: isoDate(inDays(30)), spaceport: "", reason: "" },
    jr: { kind: "incidents", reason: "", spaceport: "", from: isoDate(inDays(-60)), to: isoDate(inDays(30)), rows: null },
    au: { login: "", from: isoDate(inDays(-7)), to: isoDate(inDays(1)), rows: null },
  }),
  computed: {
    tabs() { return TABS.filter(t => this.can(t.perm)); },
    activeLaunches() { return this.launches.filter(l => !l.final); },
    unavailableForecasts() { return this.forecasts.filter(f => f.forecast && f.forecast.status === "unavailable"); },
    staffNeedsSpaceport() { return !GLOBAL_ROLES.includes(this.staffForm.role); },
  },
  methods: {
    can(p) { return !!this.me && [].concat(p).some(x => this.me.permissions.includes(x)); },
    launchesIn(status) { return this.launches.filter(l => l.status === status); },
    notify(text, type = "ok") { this.toast = { text, type }; clearTimeout(this._t); this._t = setTimeout(() => this.toast = null, 7000); },
    qs(params) { return Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined).map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&"); },

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
        this.notify(data.message || "Готово", data.warnings && data.warnings.length ? "warn" : "ok");
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
      Object.assign(this, { token: null, me: null, card: null, report: null, spaceportFilter: "" });
      this.jr.rows = null; this.au.rows = null;
    },
    async start() {
      this.me = await this.api("GET", "/api/me");
      this.tab = this.tabs[0].id;
      await this.reload();
    },
    async reload() {
      this.ref = await this.api("GET", "/api/reference");
      const sp = this.qs({ spaceport: this.spaceportFilter });
      if (this.can("view_schedule")) this.launches = await this.api("GET", `/api/launches?${this.qs({ spaceport: this.spaceportFilter, ...this.search })}`);
      if (this.can("manage_fuel")) this.batches = await this.api("GET", `/api/fuel?${sp}`);
      if (this.can(["view_reports", "view_incident_reports"])) this.reportList = await this.api("GET", "/api/reports");
      if (this.can("manage_staff")) this.staff = await this.api("GET", "/api/staff");
      if (this.can(["request_forecast", "record_weather"])) this.forecasts = await this.api("GET", `/api/forecasts?${sp}`);
      if (this.card) await this.openCard(this.card.id);
      // значения по умолчанию для выпадающих списков
      this.reg.vehicle_type_id ??= this.ref.vehicle_types[0]?.id;
      this.reg.pad_id ??= this.ref.pads[0]?.id;
      this.refuelForm.launch_id ??= this.launchesIn("fueling")[0]?.id;
      this.refuelForm.batch_id ??= this.batches[0]?.id;
      this.wx.launch_id ??= this.activeLaunches[0]?.id;
      this.fc.launch_id = this.unavailableForecasts.find(f => f.id === this.fc.launch_id)?.id ?? this.unavailableForecasts[0]?.id ?? null;
      this.inc.launch_id ??= this.launches.find(l => l.status !== "planned" && l.status !== "cancelled")?.id;
      this.padForm.spaceport_id ??= this.ref.spaceports[0]?.id;
      this.staffForm.spaceport_id ??= this.ref.spaceports[0]?.id;
      this.batchForm.spaceport_id ??= this.ref.spaceports[0]?.id;
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
    addStaff() { this.post("/api/staff", { ...this.staffForm, spaceport_id: this.staffNeedsSpaceport ? this.staffForm.spaceport_id : null }); },
    async weather() {
      try {
        this.wxResult = await this.api("POST", `/api/launches/${this.wx.launch_id}/weather`, this.wx);
        await this.reload();
      } catch (e) { this.notify(e.message, "error"); }
    },
    requestForecast(id) { this.post(`/api/launches/${id}/forecast`); },
    manualForecast() { this.post(`/api/launches/${this.fc.launch_id}/forecast/manual`, this.fc); },
    async loadJournal() {
      const q = this.qs({ from: this.jr.from, to: this.jr.to, spaceport: this.jr.spaceport, reason: this.jr.kind === "postponements" ? this.jr.reason : "" });
      try { this.jr.rows = await this.api("GET", `/api/journal/${this.jr.kind}?${q}`); } catch (e) { this.notify(e.message, "error"); }
    },
    async loadAudit() {
      try { this.au.rows = await this.api("GET", `/api/audit?${this.qs({ from: this.au.from, to: this.au.to, login: this.au.login })}`); }
      catch (e) { this.notify(e.message, "error"); }
    },
    reportQuery() { return this.qs({ from: this.rep.from, to: this.rep.to, spaceport: this.rep.spaceport, reason: this.rep.key === "postponements" ? this.rep.reason : "" }); },
    async buildReport() {
      try { this.report = await this.api("GET", `/api/reports/${this.rep.key}?${this.reportQuery()}`); }
      catch (e) { this.notify(e.message, "error"); }
    },
    async downloadCsv() {
      const res = await fetch(`/api/reports/${this.rep.key}.csv?${this.reportQuery()}`,
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
