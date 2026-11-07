/* qq-bot /console Vue 应用（阶段 1 骨架，2026-11）。
   设计依据 docs/控制台重设计-2026-11.md D.1 / E 章；主人评审修订 8 条已落实：
   · 修订 2：所有数据卡三态 —— ok(实时数据) / pending(历史累计中) / down(服务不可用)，禁止假 0；
   · 修订 3：配置改动保持「即时生效」语义（无全局保存按钮）；
   · 阶段 1 不加计数器、不动数据库，Dashboard 只消费现有 /api/status 与 /api/config。 */
"use strict";
(function () {
  function getToken() {
    try { return localStorage.getItem("qqbot_token") || ""; } catch (e) { return ""; }
  }
  const TOKEN = window.__QQ_TOKEN__ || getToken();   /* 注入的优先，其次本机存的 */
  const API = { "X-Bridge-Token": TOKEN, "X-QQ-Source": "web" };
  /* 顶栏在线状态跨组件共享（$refs 不是响应式的，直接读子组件 computed 会永远停在初值） */
  const store = Vue.reactive({ conn: "", settingsDirty: 0 });   /* settingsDirty>0 时侧栏跳转要拦截 */

  /* —— API 助手：永不抛错，返回三态 —— */
  async function api(path, opts) {
    try {
      const r = await fetch(path, Object.assign({ headers: API }, opts || {}));
      if (r.status === 401) return { state: "down", auth: true, error: "密钥不对（401）" };
      if (!r.ok) return { state: "down", error: "HTTP " + r.status };
      const data = await r.json();
      if (!data || data.ok === false) return { state: "down", error: (data && data.error) || "返回异常" };
      return { state: "ok", data };
    } catch (e) { return { state: "down", error: String(e && e.message || e) }; }
  }
  async function setCfg(body) {
    const r = await api("/api/config", { method: "POST", headers: Object.assign(
      { "Content-Type": "application/json" }, API), body: JSON.stringify(body) });
    return r.state === "ok";
  }

  /* —— 画风显示名（与 /admin 同映射：业务名为主，内核名小字）—— */
  const STY = { krea2: "写实人物", anima: "动画人物", "厚涂": "厚涂人物", 宫格: "宫格" };
  function styName(v) { return STY[v] || String(v == null ? "—" : v); }
  function fmtAgo(sec) {
    if (sec == null) return "—";
    if (sec < 60) return sec + " 秒";
    if (sec < 3600) return Math.round(sec / 60) + " 分钟";
    return (sec / 3600).toFixed(1) + " 小时";
  }

  /* —— 基础组件：三态卡片 —— */
  const CCard = {
    props: { title: String, meta: String, state: { type: String, default: "ok" },
             error: String, span: { type: String, default: "span4" } },
    emits: ["retry"],
    template: `
      <section :class="'card ' + span" data-state="state">
        <div class="card__head">
          <h2 class="card__title">{{ title }}</h2>
          <span class="card__meta" v-if="meta">{{ meta }}</span>
        </div>
        <div class="card__body">
          <slot v-if="state !== 'down'"></slot>
          <div v-else class="cstate cstate--down">
            <span class="cstate__dot"></span>
            <span>服务不可用 · {{ error || "请求失败" }}</span>
            <button class="retry" type="button" @click="$emit('retry')">重试</button>
          </div>
        </div>
      </section>`
  };

  const CSwitch = {
    props: { on: Boolean, label: String },
    emits: ["toggle"],
    template: `
      <button class="switch" type="button" role="switch"
        :aria-checked="on ? 'true' : 'false'" :aria-label="label"
        @click="$emit('toggle')"><span class="switch__dot"></span></button>`
  };

  /* —— 占位页（阶段 2+）—— */
  const Placeholder = {
    props: { page: String, note: String },
    template: `
      <div class="empty">
        <b>{{ page }} · 阶段 2 内容</b>
        {{ note || "本页在阶段 2 实现（见 docs/控制台重设计-2026-11.md F 章）。当前阶段只交付骨架与总览。" }}
      </div>`
  };

  /* —— Dashboard —— */
  const Dashboard = {
    components: { CCard, CSwitch },
    data() {
      return {
        loading: true,
        st: { state: "loading", data: null, error: "" },   /* /api/status 三态 */
        cf: { state: "loading", data: null, error: "" },   /* /api/config 三态 */
        mt: { state: "loading", data: null, error: "" },   /* /api/metrics 三态（阶段2c） */
        busy: {},                                          /* 磁贴开关点击中的 key */
        toastQ: []
      };
    },
    computed: {
      conn() {
        return this.st.state === "ok" ? "on" : (this.st.state === "down" ? "off" : "");
      },
      s() { return this.st.data || {}; },
      c() { return this.cf.data || {}; },
      comfy() { return this.s.comfy || {}; },
      send() { return this.s.send || {}; },
      circuitOpen() { return !!this.send.circuit_opened; },
      tiles() { return this.cf.data && Array.isArray(this.cf.data.tiles) ? this.cf.data.tiles : []; },
      profiles() { return this.cf.data && this.cf.data.profiles ? this.cf.data.profiles : {}; }
    },
    methods: {
      styName, fmtAgo,
      async load() {
        this.loading = true;
        this.st = await api("/api/status");
        this.cf = await api("/api/config");
        this.mt = await api("/api/metrics?hours=48");   /* 阶段2c：小时聚合趋势 */
        store.conn = this.conn;   /* 同步给顶栏（store 是 reactive 的，顶栏能跟着变） */
        this.loading = false;
        if (this.st.auth || this.cf.auth) showTokbox(true);   /* 401 → 弹密钥卡 */
      },
      spark(pts, w, h) {
        /* 手绘 sparkline：无图表库，纯 SVG polyline（HA 参考的同款形态） */
        const pts2 = pts.length === 1 ? [pts[0], pts[0]] : pts;
        const max = Math.max.apply(null, pts2.concat([1]));
        const step = w / Math.max(pts2.length - 1, 1);
        return pts2.map((v, i) => (i ? "L" : "M") + (i * step).toFixed(1) + " " +
          (h - (v / max) * (h - 6) - 3).toFixed(1)).join(" ");
      },
      toast(text, ok) {
        const id = Date.now() + Math.random();
        this.toastQ.push({ id, text, ok: !!ok });
        setTimeout(() => { this.toastQ = this.toastQ.filter(t => t.id !== id); }, 4000);
      },
      /* 磁贴总开关（修订 3：即时生效，改完重拉配置） */
      async flipTile(t) {
        if (!t.toggle || this.busy[t.toggle]) return;
        this.busy[t.toggle] = true;
        const ok = await setCfg({ key: t.toggle, value: t.toggle_on ? "关" : "开" });
        this.busy[t.toggle] = false;
        this.toast(ok ? (t.zone + (t.toggle_on ? " 已关" : " 已开")) : "没改成（见 bot 日志）", ok);
        if (ok) this.cf = await api("/api/config");
      },
      /* 预设档（一档写一组，即时应用） */
      async applyProfile(name) {
        this.toast("正在应用预设档「" + name + "」…", true);
        const ok = await setCfg({ profile: name });
        this.toast(ok ? "预设档「" + name + "」已应用" : "没改成", ok);
        if (ok) { this.cf = await api("/api/config"); this.st = await api("/api/status"); }
      },
      /* 当前档判定（与 /admin matchProfile 同语义：按组内 label 匹配） */
      isProfile(name) {
        const kvmap = this.profiles[name] || {};
        const items = {};
        ((this.cf.data && this.cf.data.groups) || []).forEach(g =>
          (g.items || []).forEach(i => { items[i.label] = i; }));
        return Object.keys(kvmap).every(k => {
          const it = items[k]; if (!it) return false;
          const want = kvmap[k];
          if (typeof want === "boolean")
            return it.type === "bool" ? !!it.value === want : String(it.display) === String(want);
          return String(it.display) === String(want) || String(it.value) === String(want);
        });
      },
      profileKv(name) {
        const kv = this.profiles[name] || {};
        return Object.keys(kv).map(k => k + "=" + kv[k]).join("　");
      }
    },
    mounted() { this.load(); },
    template: `
    <div>
      <!-- 胶囊行：实时状态（/api/status 成败即在线判定；不造假） -->
      <div class="pills">
        <span class="pill" :class="conn==='on' ? 'pill--ok' : (conn==='off' ? 'pill--danger' : '')">
          <span class="conn__dot" style="display:inline-block;width:7px;height:7px;border-radius:99px"
            :style="{background: conn==='on' ? 'var(--ok)' : (conn==='off' ? 'var(--danger)' : 'var(--text-muted)')}"></span>
          {{ st.state==='ok' ? (s.nickname || "在线") : (st.state==='loading' ? "读取中…" : "服务不可用") }}
        </span>
        <template v-if="st.state==='ok'">
          <span class="pill">群 <b>{{ (s.groups||[]).length }}</b></span>
          <span class="pill">大脑 <b>{{ s.brain || "—" }}</b></span>
          <span class="pill">人格 <b>{{ s.role || "默认" }}</b></span>
          <span class="pill" v-if="s.silent" class="pill--warn">静默中</span>
          <span class="pill" v-if="send.circuit_opened">🔴 熔断打开（剩 {{ fmtAgo(send.circuit_left_sec) }}）</span>
        </template>
      </div>

      <div class="grid">
        <!-- 运行状态 -->
        <c-card title="运行状态" meta="实时" :state="st.state" :error="st.error"
                span="span4" @retry="load">
          <template v-if="st.state==='ok'">
            <div v-if="loading" class="sk" style="height:96px"></div>
            <div class="kv" v-else>
              <span class="kv__k">机器人</span><span class="kv__v">{{ s.nickname }}（{{ s.self_id }}）</span>
              <span class="kv__k">图库</span><span class="kv__v">{{ (s.images||[]).length }} 张</span>
              <span class="kv__k">黑话</span><span class="kv__v">确认 {{ (s.slang_confirmed||[]).length }} / 待审 {{ (s.slang_pending||[]).length }}</span>
              <span class="kv__k">表达</span><span class="kv__v">确认 {{ (s.expr_confirmed||[]).length }} / 待审 {{ (s.expr_pending||[]).length }}</span>
              <span class="kv__k">记忆</span><span class="kv__v">{{ s.memory && s.memory.facts != null ? s.memory.facts + " 条" : "—" }}</span>
              <span class="kv__k">网页识图</span><span class="kv__v" :class="(s.webai&&s.webai.ok)?'kv__v--ok':''">{{ s.webai ? (s.webai.ok ? "可用" : "不可用") : "—" }}</span>
            </div>
          </template>
          <div v-else-if="st.state==='loading'" class="sk" style="height:96px"></div>
        </c-card>

        <!-- 现画（ComfyUI 离线是可选依赖的常态 → warn 而非 down） -->
        <c-card title="现画" meta="ComfyUI" :state="st.state" :error="st.error"
                span="span4" @retry="load">
          <template v-if="st.state==='ok'">
            <div class="kv">
              <span class="kv__k">ComfyUI</span>
              <span class="kv__v" :class="comfy.server_up ? 'kv__v--ok' : 'kv__v--warn'">
                {{ comfy.server_up ? "在线" : "离线" }}{{ comfy.enabled ? "" : "（现画已关）" }}</span>
              <span class="kv__k">画图档</span><span class="kv__v">{{ styName(comfy.default_style) }}（{{ comfy.default_style || "—" }}）</span>
              <span class="kv__k">队列</span><span class="kv__v">{{ comfy.drawing ? "渲染中" : "空闲" }}　排队 {{ comfy.queued == null ? "—" : comfy.queued }}</span>
              <span class="kv__k">本小时</span><span class="kv__v">{{ comfy.done_this_hour == null ? "—" : comfy.done_this_hour }} / {{ comfy.max_per_hour == null ? "—" : comfy.max_per_hour }}</span>
            </div>
          </template>
          <div v-else-if="st.state==='loading'" class="sk" style="height:96px"></div>
        </c-card>

        <!-- 发送与熔断（send_stats 实时计数） -->
        <c-card title="发送与熔断" meta="实时" :state="st.state" :error="st.error"
                span="span4" @retry="load">
          <template v-if="st.state==='ok'">
            <div class="kv">
              <span class="kv__k">每分钟</span><span class="kv__v">{{ send.per_minute == null ? "—" : send.per_minute }}</span>
              <span class="kv__k">本小时</span><span class="kv__v">{{ send.per_hour == null ? "—" : send.per_hour }}</span>
              <span class="kv__k">熔断</span>
              <span class="kv__v" :class="circuitOpen ? 'kv__v--bad' : 'kv__v--ok'">
                {{ circuitOpen ? "已打开（剩 " + fmtAgo(send.circuit_left_sec) + "）" : "正常" }}</span>
              <span class="kv__k">连败计数</span><span class="kv__v">{{ send.circuit_fails == null ? "—" : send.circuit_fails }}</span>
            </div>
          </template>
          <div v-else-if="st.state==='loading'" class="sk" style="height:96px"></div>
        </c-card>

        <!-- 消息量趋势（阶段 2c 计数器①）：真实小时聚合，刚上线数据少=真实的小数字 -->
        <c-card title="消息量" meta="近 48 小时" :state="mt.state" :error="mt.error" span="span6" @retry="load">
          <template v-if="mt.state === 'ok'">
            <div class="trend" v-if="mt.data.msg.length">
              <div class="trend__chart">
                <svg viewBox="0 0 480 90" preserveAspectRatio="none" class="trend__svg">
                  <path :d="spark(mt.data.msg.map(m => m.recv), 480, 80)" class="trend__line trend__line--recv"></path>
                  <path :d="spark(mt.data.msg.map(m => m.sent), 480, 80)" class="trend__line trend__line--sent"></path>
                </svg>
              </div>
              <div class="trend__legend">
                <span><i class="dot dot--recv"></i>收 {{ mt.data.today.recv }}</span>
                <span><i class="dot dot--sent"></i>发 {{ mt.data.today.sent }}</span>
              </div>
            </div>
            <div v-else class="cstate cstate--pending"><span class="cstate__dot"></span>
              <span>计数器刚上线，还没有任何小时数据 —— 每条收发都会累计进来。</span></div>
          </template>
        </c-card>

        <!-- 模型调用趋势（阶段 2c 计数器②） -->
        <c-card title="模型调用" meta="近 48 小时" :state="mt.state" :error="mt.error" span="span6" @retry="load">
          <template v-if="mt.state === 'ok'">
            <div class="trend" v-if="mt.data.calls.length">
              <div class="trend__chart">
                <svg viewBox="0 0 480 90" preserveAspectRatio="none" class="trend__svg">
                  <path :d="spark(mt.data.calls.map(c => c.calls), 480, 80)" class="trend__line trend__line--recv"></path>
                </svg>
              </div>
              <div class="trend__legend">
                <span><i class="dot dot--recv"></i>今日 {{ mt.data.calls_today }} 次</span>
                <span v-if="mt.data.fails_today"><i class="dot dot--fail"></i>失败 {{ mt.data.fails_today }}</span>
              </div>
            </div>
            <div v-else class="cstate cstate--pending"><span class="cstate__dot"></span>
              <span>计数器刚上线，还没有任何小时数据。</span></div>
          </template>
        </c-card>

        <!-- 快捷总开关（zone_tiles 磁贴，即时生效） -->
        <c-card title="分区总开关" meta="改动即时生效" :state="cf.state" :error="cf.error"
                span="span8" @retry="load">
          <template v-if="cf.state==='ok'">
            <div class="ztiles">
              <div v-for="t in tiles" :key="t.zone" class="ztile" :class="{ 'ztile--off': t.toggle && !t.toggle_on }">
                <div class="ztile__top">
                  <span class="ztile__name" :title="'进「' + t.zone + '』分区（阶段 2 开放页内跳转）'">{{ t.zone }}</span>
                  <span class="ztile__sw" v-if="t.toggle">
                    <c-switch :on="!!t.toggle_on" :label="t.zone + ' 总开关'"
                      @toggle="flipTile(t)"></c-switch>
                  </span>
                </div>
                <div class="ztile__stat" v-if="t.stat">{{ t.stat }}</div>
                <div class="ztile__meta">{{ (t.groups||[]).length }} 张卡</div>
              </div>
            </div>
          </template>
          <div v-else-if="cf.state==='loading'" class="sk" style="height:120px"></div>
        </c-card>

        <!-- 预设档 -->
        <c-card title="预设档" meta="一档写一组，点了就生效" :state="cf.state" :error="cf.error"
                span="span4" @retry="load">
          <template v-if="cf.state==='ok'">
            <div class="tiles">
              <button v-for="(kv, name) in profiles" :key="name" type="button"
                class="tile" :class="{ 'tile--on': isProfile(name) }" @click="applyProfile(name)">
                <span class="tile__top">
                  <span class="tile__name">{{ name }}</span>
                  <span class="tag" :class="{ 'tag--on': isProfile(name) }">{{ isProfile(name) ? "当前" : "应用" }}</span>
                </span>
                <span class="tile__kv">{{ profileKv(name) }}</span>
              </button>
            </div>
          </template>
          <div v-else-if="cf.state==='loading'" class="sk" style="height:120px"></div>
        </c-card>
      </div>

      <!-- toast 栈 -->
      <div class="toasts">
        <div v-for="t in toastQ" :key="t.id" class="toast" :class="t.ok ? 'toast--ok' : 'toast--err'">{{ t.text }}</div>
      </div>
    </div>`
  };

  /* —— 人格中心（阶段 2 批 2a）—— */
  const KIND_NAME = { observation: "观察", opinion: "观点", open_loop: "悬念", follow_up: "跟进" };
  const KIND_CLS = { observation: "tag--obs", opinion: "tag--op", open_loop: "tag--loop", follow_up: "tag--fup" };

  const PersonaPage = {
    components: { CCard },
    props: { initialTab: { type: String, default: "" } },
    data() {
      return {
        KIND_NAME, KIND_CLS,   /* 模板里 KIND_NAME[kind] 只能读实例属性（_ctx），模块常量要挂进来 */
        state: "loading", error: "",
        personas: [], active: "", sel: "",
        tab: "mood", groups: [], gid: "",
        mood: null, rstat: null, ritems: null, rtotal: 0,
        mem: null, memLoading: false,
        confirmOn: false, busy: false, toastQ: []
      };
    },
    methods: {
      async api(path, opts) {
        try {
          const r = await fetch(path, Object.assign({ headers: API }, opts || {}));
          if (r.status === 401) { showTokbox(true); return { state: "down", auth: true, error: "401" }; }
          if (!r.ok) return { state: "down", error: "HTTP " + r.status };
          const d = await r.json();
          return d && d.ok !== false ? { state: "ok", data: d } : { state: "down", error: d.error || "返回异常" };
        } catch (e) { return { state: "down", error: String(e && e.message || e) }; }
      },
      toast(text, ok) {
        const id = Date.now() + Math.random();
        this.toastQ.push({ id, text, ok: !!ok });
        setTimeout(() => { this.toastQ = this.toastQ.filter(t => t.id !== id); }, 4000);
      },
      async load() {
        this.state = "loading";
        /* /api/personas 只在 POST 分支注册（同 /admin 人格管理卡），GET 是 404 */
        const p = await this.api("/api/personas", { method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, API),
          body: JSON.stringify({ op: "list" }) });
        if (p.state !== "ok") { this.state = "down"; this.error = p.error; return; }
        this.personas = p.data.list || [];
        this.active = p.data.active || "";
        this.sel = this.sel || this.active;
        const g = await this.api("/api/groups");
        /* /api/groups 返回 [{group_id, name, members}]——取 group_id，显示用群名 */
        this.groups = (g.state === "ok" && g.data.groups) || [];
        this.groups = this.groups.map(x =>
          typeof x === "object" ? { id: String(x.group_id), name: x.name || x.group_id } : { id: String(x), name: String(x) });
        if (!this.gid && this.groups.length) this.gid = this.groups[0].id;
        this.state = "ok";
        this.loadTab();
      },
      async loadTab() {
        if (this.tab === "mood") {
          if (!this.gid) { this.mood = { state: "pending" }; return; }
          this.mood = await this.api("/api/mood_read?gid=" + this.gid);
        } else if (this.tab === "reflect") {
          /* 修订 8：观点/反思严格按当前选中人格过滤，切人格即切视图 */
          const q = "persona=" + encodeURIComponent(this.sel) + "&limit=50";
          const [s, it] = await Promise.all([
            this.api("/api/reflect_stat?persona=" + encodeURIComponent(this.sel)),
            this.api("/api/reflect_items?" + q)]);
          this.rstat = s; this.ritems = it;
        } else if (this.tab === "memory") {
          this.memLoading = true;
          this.mem = this.gid ? await this.api("/api/memory?group=" + this.gid + "&limit=30") : { state: "pending" };
          this.memLoading = false;
        }
      },
      pick(p) { if (this.sel !== p) { this.sel = p; this.loadTab(); } },
      pickTab(t) {
        if (this.tab === t) return;
        this.tab = t;
        try { history.replaceState(null, "", "#/persona/" + t); } catch (e) {}
        this.loadTab().catch(function (e) { window.__qqErr && window.__qqErr(e); });
      },
      askSwitch() { this.confirmOn = true; },
      async doSwitch() {
        this.confirmOn = false;
        this.busy = true;
        const r = await this.api("/api/config", { method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, API),
          body: JSON.stringify({ key: "ai.role", value: this.sel }) });
        this.busy = false;
        this.toast(r.state === "ok" ? "已切换到「" + this.sel + "」" : "没切成（" + (r.error || "?") + "）", r.state === "ok");
        if (r.state === "ok") { this.active = this.sel; this.load(); }
      },
      ago(sec) {
        if (sec == null) return "—";
        if (sec < 60) return sec + " 秒前";
        if (sec < 3600) return Math.round(sec / 60) + " 分钟前";
        if (sec < 86400) return Math.round(sec / 3600) + " 小时前";
        return Math.round(sec / 86400) + " 天前";
      },
      pct(v) { return Math.round((Number(v) || 0) * 100); }
    },
    mounted() {
      if (this.initialTab) this.tab = this.initialTab;
      this.load();
    },
    template: `
    <div>
      <div class="pwrap">
        <!-- 左：人格列表 -->
        <aside class="plist card">
          <div class="card__head"><h2 class="card__title">人格</h2>
            <span class="card__meta">{{ personas.length }} 个</span></div>
          <div class="card__body plist__body">
            <button v-for="p in personas" :key="p.name" type="button"
              class="pitem" :class="{ 'pitem--sel': p.name === sel }" @click="pick(p.name)">
              <span class="pitem__name">{{ p.name }}</span>
              <span class="tag" v-if="p.name === active">在岗</span>
            </button>
            <div class="cstate cstate--down" v-if="state==='down'">
              <span class="cstate__dot"></span><span>{{ error }}</span>
              <button class="retry" type="button" @click="load">重试</button></div>
            <div class="sk" v-if="state==='loading'" style="height:120px"></div>
          </div>
        </aside>
        <!-- 右：详情三页签 -->
        <section class="pdetail">
          <div class="card">
            <div class="card__head">
              <h2 class="card__title">{{ sel || "—" }}</h2>
              <span class="card__meta">{{ sel === active ? "当前在岗人格" : "查看中（未在岗）" }}</span>
              <button class="btn" type="button" :disabled="sel === active || busy"
                @click="askSwitch">{{ sel === active ? "在岗中" : (busy ? "切换中…" : "切到此人格") }}</button>
            </div>
            <div class="ptabs">
              <button v-for="t in [['mood','情绪'],['reflect','观点与反思'],['memory','记忆与画像']]"
                :key="t[0]" type="button" class="ptab" :class="{ 'ptab--on': tab === t[0] }"
                @click="pickTab(t[0])">{{ t[1] }}</button>
            </div>
            <div class="card__body">
              <!-- 情绪 -->
              <template v-if="tab === 'mood'">
                <div v-if="mood && mood.state === 'ok'" class="moodgrid">
                  <div v-for="a in mood.data.axes" :key="a.key" class="moodaxis">
                    <span class="moodaxis__name">{{ a.name }}<i class="moodaxis__key">{{ a.key }}</i></span>
                    <span class="moodbar"><i :style="{ width: pct(a.value) + '%' }"
                      :class="{ 'moodbar--neg': a.value < 0 }"></i></span>
                    <span class="moodaxis__v">{{ a.value.toFixed(2) }}</span>
                  </div>
                  <p class="pnote">数值 0~1（情绪价 -1~1），按小时半衰期向基线回落；按群存放（当前群 {{ (groups.find(function(x){return x.id===gid;}) || {}).name || gid || "—" }}）。</p>
                </div>
                <div v-else-if="mood && mood.state === 'pending'" class="cstate cstate--pending">
                  <span class="cstate__dot"></span><span>还没有群会话，情绪按群记录，无群可查。</span></div>
                <div v-else class="cstate cstate--down"><span class="cstate__dot"></span>
                  <span>读取失败</span><button class="retry" type="button" @click="loadTab">重试</button></div>
              </template>
              <!-- 观点与反思 -->
              <template v-if="tab === 'reflect'">
                <div v-if="rstat && rstat.state === 'ok' && ritems && ritems.state === 'ok'">
                  <div class="pills" style="margin-bottom:12px">
                    <span class="pill">累计 <b>{{ rstat.data.stat.total }}</b> 条（全人格）</span>
                    <span class="pill" v-for="b in rstat.data.stat.by" :key="b.kind + b.status"
                      :class="b.persona || true ? '' : ''">
                      <i :class="KIND_CLS[b.kind] || ''">{{ KIND_NAME[b.kind] || b.kind }}</i>
                      {{ b.status }} <b>{{ b.n }}</b></span>
                    <span class="pill">今日反思 <b>{{ rstat.data.daily_runs }}</b> 次</span>
                  </div>
                  <p class="pnote">以下为「{{ sel }}」名下的观点与反思（persona 过滤，切人格即换视图）。</p>
                  <div v-if="!ritems.data.items.length" class="empty" style="padding:26px 10px">
                    <b>「{{ sel }}」名下暂无反思条目</b>Reflection 按 60 秒巡检群消息提炼，尚未产生属于该人格的新观点。</div>
                  <div v-for="it in ritems.data.items" :key="it.id" class="ritem">
                    <div class="ritem__top">
                      <span class="tag" :class="KIND_CLS[it.kind]">{{ KIND_NAME[it.kind] || it.kind }}</span>
                      <span class="ritem__topic" v-if="it.topic">{{ it.topic }}</span>
                      <span class="card__meta">{{ ago(it.updated_ago_sec) }} · 用过 {{ it.use_count || 0 }} 次</span>
                    </div>
                    <div class="ritem__content">{{ it.content }}</div>
                  </div>
                </div>
                <div v-else class="cstate cstate--down"><span class="cstate__dot"></span>
                  <span>读取失败</span><button class="retry" type="button" @click="loadTab">重试</button></div>
              </template>
              <!-- 记忆与画像 -->
              <template v-if="tab === 'memory'">
                <p class="pnote">⚠️ 群友画像是<b>所有人格共享的知识</b>（设计语义，非泄漏）；按群存放，与「观点」分开。</p>
                <div class="pills" style="margin-bottom:10px">
                  <button v-for="g in groups" :key="g.id" type="button" class="pill"
                    :class="{ 'pill--sel': g.id === gid }" @click="gid = g.id; loadTab()">
                    {{ g.name }}</button>
                </div>
                <div v-if="mem && mem.state === 'ok'">
                  <div v-for="f in (mem.data.facts || mem.data.items || []).slice(0, 30)" :key="f.id || f.content"
                    class="ritem"><div class="ritem__content">{{ f.name ? f.name + "：" : "" }}{{ f.content }}
                    <span class="card__meta" v-if="f.kind">（{{ f.kind }}）</span></div></div>
                  <div v-if="!(mem.data.facts || mem.data.items || []).length" class="empty" style="padding:26px 10px">
                    <b>该群暂无记忆条目</b>长期记忆由群聊积累。</div>
                </div>
                <div v-else-if="memLoading" class="sk" style="height:80px"></div>
                <div v-else class="cstate cstate--down"><span class="cstate__dot"></span>
                  <span>读取失败</span><button class="retry" type="button" @click="loadTab">重试</button></div>
              </template>
            </div>
          </div>
        </section>
      </div>
      <!-- 切换人格：危险确认（C.4） -->
      <div class="tokbox" v-if="confirmOn">
        <div class="tokbox__card">
          <b>切换到「{{ sel }}」？</b>
          <p>Reflection 游标与观点按人格记录：切换后，新反思会归入「{{ sel }}」名下；
          各群的群友画像（记忆）为共享知识，不受影响。</p>
          <div class="tokbox__row">
            <button class="retry" type="button" @click="confirmOn = false">取消</button>
            <button class="btn" type="button" @click="doSwitch">确认切换</button>
          </div>
        </div>
      </div>
      <div class="toasts">
        <div v-for="t in toastQ" :key="t.id" class="toast" :class="t.ok ? 'toast--ok' : 'toast--err'">{{ t.text }}</div>
      </div>
    </div>`,
    watch: { sel() {} }
  };


  /* —— 通用旋钮分区页（阶段 2 批 2b）：消费 /api/config 的 groups，一 group 一卡 —— */
  const KnobsPage = {
    components: { CCard, CSwitch },
    props: { zone: { type: String, default: "" },
             zones: { type: String, default: "" },   /* 逗号分隔多分区（bot.py ZONES 的 zone 名） */
             intro: { type: String, default: "" } },
    data() { return { state: "loading", error: "", groups: [], vmap: {}, busy: {}, toastQ: [] }; },
    computed: {
      mine() {
        /* 只渲染 bot.py ZONES 归进本页分区的卡（zone 名以后端为准） */
        const zs = (this.zones || this.zone || "").split(",").map(x => x.trim()).filter(Boolean);
        return (this.groups || []).filter(g => zs.indexOf((this.$root.zonemap || {})[g.group]) >= 0);
      }
    },
    methods: {
      async load() {
        this.state = "loading";
        const r = await this.api("/api/config");
        if (r.state !== "ok") { this.state = "down"; this.error = r.error; return; }
        this.groups = r.data.groups || [];
        this.$root.zonemap = {};
        (r.data.zones || []).forEach(z => (z.groups || []).forEach(g => { this.$root.zonemap[g] = z.zone; }));
        /* key→value 全量表：depends_on 联动显隐用（同 /admin depHidden 语义） */
        this.vmap = {};
        this.groups.forEach(g => (g.items || []).forEach(i => { this.vmap[i.key || i.label] = i.value; }));
        this.state = "ok";
      },
      visItems(g) {
        /* 复刻 /admin brainSplit + depHidden：只列当前大脑用得上的项（主人 2026-10-20 定） */
        let cur = String(this.vmap["ai.provider"] || "").toLowerCase();
        if (cur === "agent") cur = "qoder";
        const LOCAL = { qoder: 1, dsh: 1, workbuddy: 1, zcode: 1 };
        const nb = x => {
          if (typeof x === "boolean") return x;
          const t = String(x == null ? "" : x).trim().toLowerCase();
          return t === "true" || t === "1" || t === "开" || t === "on" || t === "yes";
        };
        const vis = [];
        (g.items || []).forEach(it => {
          if (it.web_hidden) return;
          const b = it.brain;
          if (b && !(b === cur || (b === "api" && cur && !LOCAL[cur]))) return;
          const d = it.depends_on;
          if (d && d.key) {
            const v = this.vmap[d.key];
            if (v !== undefined && v !== null && nb(v) !== nb(d.want)) return;
          }
          vis.push(it);
        });
        return vis;
      },
      isHalf(it) { return it.type === "bool" || (it.type === "enum" && (it.values || []).length <= 4); },
      advItems(g) {
        /* 先管大体（修订5/主人一贯反馈）：数值/文本调参收「高级参数」折叠，bool/enum/密钥留主区 */
        return this.visItems(g).filter(it =>
          it.type === "int" || it.type === "float" || (it.type === "str" && !it.multiline));
      },
      mainItems(g) {
        return this.visItems(g).filter(it =>
          !(it.type === "int" || it.type === "float" || (it.type === "str" && !it.multiline)));
      },
      async api(path, opts) {
        try {
          const r = await fetch(path, Object.assign({ headers: API }, opts || {}));
          if (r.status === 401) { showTokbox(true); return { state: "down", auth: true, error: "401" }; }
          if (!r.ok) return { state: "down", error: "HTTP " + r.status };
          const d = await r.json();
          return d && d.ok !== false ? { state: "ok", data: d } : { state: "down", error: d.error || "返回异常" };
        } catch (e) { return { state: "down", error: String(e && e.message || e) }; }
      },
      toast(text, ok) {
        const id = Date.now() + Math.random();
        this.toastQ.push({ id, text, ok: !!ok });
        setTimeout(() => { this.toastQ = this.toastQ.filter(t => t.id !== id); }, 4000);
      },
      async setKnob(it, value) {
        const k = it.key || it.label;
        if (this.busy[k]) return;
        this.busy[k] = true;
        const r = await this.api("/api/config", { method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, API),
          body: JSON.stringify({ key: k, value: value }) });
        this.busy[k] = false;
        this.toast(r.state === "ok"
          ? ((it.label || k) + " → " + value + (r.data && r.data.message ? "" : ""))
          : "没改成：" + (r.data && r.data.message || r.error || "?"),
          r.state === "ok");
        if (r.state === "ok") this.load();
      }
    },
    mounted() { this.load(); },
    template: `
    <div>
      <p class="pnote" v-if="intro">{{ intro }}</p>
      <div class="grid" v-if="state === 'ok'">
        <c-card v-for="g in mine" :key="g.group" :title="g.title || g.group"
          :meta="mainItems(g).length + ' 项' + (advItems(g).length ? '（另有 ' + advItems(g).length + ' 高级）' : '') + ' · 即时生效'" span="span6" :state="'ok'">
          <div class="krowgrid">
          <div class="knobrow" :class="{ 'knobrow--full': !isHalf(it) }" v-for="it in mainItems(g)" :key="it.key || it.label"
            :title="it.desc || ''">
            <div class="knobrow__lab">
              <span class="knobrow__name">{{ it.label }}</span>
              <span class="tag" v-if="it.apply === 'restart'">要重启</span>
              <span class="tag" v-if="it.writable === false || it.type === 'secret'">只读</span>
            </div>
            <div class="knobrow__ctl">
              <!-- 只读/密钥：显示状态不显示控件 -->
              <span class="ro" v-if="it.writable === false || it.type === 'secret'">{{ it.mask ? (it.value ? "已配置" : "未配置") : it.display }}</span>
              <!-- 开关 -->
              <template v-else-if="it.type === 'bool'">
                <c-switch :on="!!it.value" :label="it.label" @toggle="setKnob(it, it.value ? '关' : '开')"></c-switch>
              </template>
              <!-- 档位：≤4 项分段按钮；>4 项（模型清单那种）用下拉 -->
              <template v-else-if="it.type === 'enum'">
                <div class="seg" v-if="(it.values || []).length <= 4">
                  <button v-for="v in it.values" :key="v" type="button" class="seg__b"
                    :class="{ 'seg__b--on': String(it.value) === String(v) }"
                    @click="setKnob(it, v)">{{ v }}</button>
                </div>
                <select v-else class="field knobrow__select" :value="it.value"
                  @change="setKnob(it, $event.target.value)">
                  <option v-for="v in it.values" :key="v" :value="v"
                    :selected="String(it.value) === String(v)">{{ v }}</option>
                  <option v-if="it.value != null && (it.values || []).indexOf(it.value) < 0"
                    :value="it.value">{{ it.value }}（当前）</option>
                </select>
              </template>
              <!-- 数字/文本：输入框 + 保存 -->
              <template v-else-if="it.type === 'int' || it.type === 'float' || it.type === 'str'">
                <input class="field knobrow__input" type="text" :id="'kb_' + (it.key || it.label)"
                  :value="it.value == null ? '' : it.value">
                <button class="retry" type="button" @click="setKnob(it, document.getElementById('kb_' + (it.key || it.label)).value)">保存</button>
              </template>
              <span v-else class="ro">{{ it.display }}</span>
            </div>
          </div>
          </div>
          <details class="foldadv" v-if="advItems(g).length">
            <summary>高级参数（{{ advItems(g).length }}）</summary>
            <div class="krowgrid foldadv__body">
              <div class="knobrow" :class="{ 'knobrow--full': !isHalf(it) }" v-for="it in advItems(g)" :key="it.key || it.label"
                :title="it.desc || ''">
                <div class="knobrow__lab">
                  <span class="knobrow__name">{{ it.label }}</span>
                  <span class="tag" v-if="it.apply === 'restart'">要重启</span>
                  <span class="tag" v-if="it.writable === false || it.type === 'secret'">只读</span>
                </div>
                <div class="knobrow__ctl">
                  <template v-if="it.type === 'int' || it.type === 'float' || it.type === 'str'">
                    <input class="field knobrow__input" type="text" :id="'kb_' + (it.key || it.label)"
                      :value="it.value == null ? '' : it.value">
                    <button class="retry" type="button" @click="setKnob(it, document.getElementById('kb_' + (it.key || it.label)).value)">保存</button>
                  </template>
                  <span v-else class="ro">{{ it.display }}</span>
                </div>
              </div>
            </div>
          </details>
          <p class="pnote" v-if="!visItems(g).length">这组暂无可调项。</p>
        </c-card>
      </div>
      <div class="empty" v-else-if="state === 'loading'"><b>读取配置中…</b></div>
      <div class="empty" v-else-if="state === 'down'"><b>服务不可用</b>{{ error }}
        <div><button class="retry" type="button" @click="load">重试</button></div></div>
      <div class="toasts">
        <div v-for="t in toastQ" :key="t.id" class="toast" :class="t.ok ? 'toast--ok' : 'toast--err'">{{ t.text }}</div>
      </div>
    </div>`
  };

  /* 分类一句话描述（二级 UI 评审：分类标题/描述/数量层级） */
  const CAT_DESC = {
    "大脑": "大脑后端、模型选择与联网搜索",
    "识图": "聊天看图用的视觉模型链",
    "人格": "人格相关的行为参数（编辑在「人格中心」）",
    "语音": "语音合成的触发与声线",
    "私聊": "好友私聊的行为边界",
    "管理": "静默、入群欢迎与管理命令",
    "只读": "只读的连接与密钥状态",
    "行为开关": "插话、冷场开场、看图闲聊的触发开关",
    "黑话": "网络用语的确认与自动学习",
    "表达": "这个群什么场面习惯怎么说话（例句取自真实群聊，审核后生效）",
    "时机": "开口时机的概率门/退避/等待（默认关，开了才生效）",
    "会话服务": "合批、状态卡、链接速览等辅助",
    "现画": "ComfyUI 现场生成的档位、路由与权限",
    "图库": "即发图库与配图门控",
    "工作流选择": "各画风档使用的工作流文件",
    "反推与画同款": "看图反推提示词、认人与图生图",
    "视频解析": "B站 / 抖音链接解析",
    "视频生成": "H3 图生视频链路",
  };
  /* 强制入「高级参数」的低频项（主人 2026-11-01：现画太复杂——旋钮不删，只调分层） */
  const ADV_FORCE = {
    "comfyui.styles.krea2.engine": 1, "comfyui.fallback_style": 1,
    "moss.auto_reverse_private": 1, "comfyui.workflow_dir": 1,
    "comfyui.subject_lookup": 1, "comfyui.subject_backend": 1,
    "comfyui.enhance": 1, "comfyui.enhance_provider": 1, "comfyui.artist_pick": 1,
    "comfyui.delete_after_send": 1, "comfyui.default_aspect": 1, "comfyui.aspect_words": 1,
  };
  /* 行内说明：取 desc 首句（避免长篇说明反向膨胀），title 悬停看全文 */

  /* —— 系统设置·配置工作台（设计 docs/系统设置重设计-审查.md，主人已批准含四项修订）——
     三层：全局侧栏(不动) → 页内左分类导航(7组) → 右单列紧凑配置行。
     保存语义与 /admin 一致（单键 POST 即时生效）；dirty 未保存守卫（修订③）；
     picker 拉取/测速按真实字段接线（修订④：chat.first/chat.sec/verdict/advice）。 */
  const SettingsPage = {
    components: { CSwitch },
    props: { catDefs: { type: Array, default: null } },
    data() {
      return {
        state: "loading", error: "", groups: [], vmap: {},
        cat: "大脑", q: "",
        pendingCat: "", catInitDone: false,
        dirty: {},   /* key → 用户输入值；与 API 值不同即 dirty（修订③） */
        mpick: { on: false, title: "", cnt: "", models: [], target: null },
        probing: {}, toastQ: [],
        /* Moss 工具（阶段3b→七）：提交任务给 bot 队列，出图发到所选会话 */
        mtool: "",                       /* "" | "rev" | "cmp" */
        sessions: [], submitGid: "", busy: false, doneMsg: "", err: "",
        revFocus: "", revDetail: "medium", cmpText: "", cmpChecks: ["fill", "unify", "types"], cmpExtra: "", cmpBrief: false
      };
    },
    computed: {
      cats() {
        if (this.catDefs) {
          return this.catDefs
            .map(d => ({ group: d.name, name: d.name, groups: d.groups }))
            .filter(c => c.groups.some(gn => this.groups.some(x => x.group === gn)))
            .map(c => Object.assign(c, { n: this.visSumOf(c.groups) }));
        }
        const order = ["大脑", "识图", "人格", "语音", "私聊", "管理", "只读"];
        const alias = { "大脑": "大脑与模型", "只读": "只读信息" };
        return order.filter(g => this.groups.some(x => x.group === g)).map(g => {
          const grp = this.groups.find(x => x.group === g);
          return { group: g, name: alias[g] || g, n: this.visItems(grp).length };
        });
      },
      cur() { return this.groups.find(x => x.group === this.cat) || null; },
      curDesc() { return CAT_DESC[this.cat] || ""; },
      rowGroups() {
        const def = this.catDefs
          ? this.catDefs.find(x => x.name === this.cat)
          : (this.cat ? { name: this.cat, groups: [this.cat] } : null);
        if (!def) return [];
        const out = [];
        def.groups.forEach(gn => {
          const g = this.groups.find(x => x.group === gn);
          if (!g) return;
          const vis = this.visItems(g);
          const f = it => !this.q ||
            ((it.label || "") + " " + (it.desc || "") + " " + (it.key || "")).toLowerCase()
              .indexOf(this.q.toLowerCase()) >= 0;
          out.push({ g: g, title: g.title || gn, vis: vis,
                     rows: vis.filter(it => f(it) && !this.isAdv(it)),
                     adv: vis.filter(it => f(it) && this.isAdv(it)) });
        });
        return out;
      },
      totalHit() { return this.rowGroups.reduce((a, r) => a + r.rows.length + r.adv.length, 0); },
      dirtyCount() { return Object.keys(this.dirty).length; }
    },
    watch: {
      /* dirty 数量同步给全局 store：侧栏跳转前 App 层据此拦截（修订③） */
      dirtyCount(n) { store.settingsDirty = n; },
      /* cat 归位（时序无关）：cats 随 groups/catDefs 变化，归位放这里每次都对 */
      cats: {
        immediate: true,
        handler(cs) {
          /* 深链优先：首个非空 cats 到达时消费一次（之后用户手动切换不受影响） */
          if (!this.catInitDone && this.pendingCat && cs.some(c => c.group === this.pendingCat)) {
            this.cat = this.pendingCat;
            this.catInitDone = true;
            return;
          }
          /* 🔴 数据未到（cs 空）时绝不能动 cat：清空后再归位会把深链/默认值顶掉
             （2026-11-02 实测：默认云端 Moss / 深链 都被这里清成「现画」） */
          if (!cs.length) return;
          if (!cs.some(c => c.group === this.cat)) this.cat = cs[0].group;
        }
      }
    },
    methods: {
      async api(path, opts) {
        try {
          const r = await fetch(path, Object.assign({ headers: API }, opts || {}));
          if (r.status === 401) { showTokbox(true); return { state: "down", auth: true, error: "401" }; }
          if (!r.ok) return { state: "down", error: "HTTP " + r.status };
          const d = await r.json();
          return d && d.ok !== false ? { state: "ok", data: d } : { state: "down", error: d.error || "返回异常", data: d };
        } catch (e) { return { state: "down", error: String(e && e.message || e) }; }
      },
      async load() {
        this.state = "loading";
        const r = await this.api("/api/config");
        if (r.state !== "ok") { this.state = "down"; this.error = r.error; return; }
        this.groups = r.data.groups || [];
        this.vmap = {};
        this.groups.forEach(g => (g.items || []).forEach(i => { this.vmap[i.key || i.label] = i.value; }));
        this.state = "ok";
      },
      visItems(g) {
        if (!g) return [];
        let cur = String(this.vmap["ai.provider"] || "").toLowerCase();
        if (cur === "agent") cur = "qoder";
        const LOCAL = { qoder: 1, dsh: 1, workbuddy: 1, zcode: 1 };
        const nb = x => {
          if (typeof x === "boolean") return x;
          const t = String(x == null ? "" : x).trim().toLowerCase();
          return t === "true" || t === "1" || t === "开" || t === "on" || t === "yes";
        };
        const vis = [];
        (g.items || []).forEach(it => {
          if (it.web_hidden) return;
          const b = it.brain;
          if (b && !(b === cur || (b === "api" && cur && !LOCAL[cur]))) return;
          const d = it.depends_on;
          if (d && d.key) {
            const v = this.vmap[d.key];
            if (v !== undefined && v !== null && nb(v) !== nb(d.want)) return;
          }
          vis.push(it);
        });
        return vis;
      },
      isAdv(it) {
        /* 主区例外①：配自定义大脑的必经入口，不折叠（主人 2026-11-01 指示） */
        if (it.key === "ai.providers.custom.url" || it.key === "ai.providers.custom.key"
            || it.key === "moss.model" || it.key === "moss.api_key") return false;
        /* 主区例外②：现画低频调参强制入折叠（主人 2026-11-01 "有些功能会不会不需要" → G6 分层） */
        if (ADV_FORCE[it.key]) return true;
        return it.type === "int" || it.type === "float" || (it.type === "str" && !it.multiline);
      },
      visSumOf(groups) {
        return groups.reduce((a, gn) => {
          const g = this.groups.find(x => x.group === gn);
          return a + (g ? this.visItems(g).length : 0);
        }, 0);
      },
      isDirty(it) {
        const k = it.key || it.label;
        return this.dirty[k] !== undefined && this.dirty[k] !== String(it.value == null ? "" : it.value);
      },
      onInput(it, ev) {
        const k = it.key || it.label;
        const v = ev.target.value;
        if (v === String(it.value == null ? "" : it.value)) delete this.dirty[k];
        else this.dirty[k] = v;
      },
      dirtyOf(it) {
        const k = it.key || it.label;
        return this.dirty[k] !== undefined ? this.dirty[k] : (it.value == null ? "" : it.value);
      },
      desc1(it) {
        const d = String(it.desc || "");
        if (!d) return "";
        const first = d.split(/[。！？]/)[0].replace(/\*\*/g, "").trim();
        return first.length > 64 ? first.slice(0, 63) + "…" : first;
      },
      /* —— Moss 工具（阶段3b→七）：提交任务给 bot 队列 —— */
      doSubmitReve(ev) {
        const f = ev && ev.target && ev.target.files && ev.target.files[0];
        if (!f) return;
        this.busy = true; this.doneMsg = ""; this.err = "";
        const fr = new FileReader();
        fr.onload = () => {
          this.busy = false;
          this.doSubmit(String(fr.result || "").split(",").pop(),
                        (f.name.split(".").pop() || "png").toLowerCase(),
                        this.revDetail);
        };
        fr.onerror = () => { this.busy = false; this.err = "读取文件失败"; };
        fr.readAsDataURL(f);
      },
      openMTool(t) {
        this.mtool = t;
        this.doneMsg = ""; this.err = "";
        this.api("/api/groups").then(g => {
          this.sessions = (g.state === "ok" && g.data.groups) || [];
          if (!this.submitGid && this.sessions.length) this.submitGid = String(this.sessions[0].group_id);
        });
      },
      doSubmit(imageB64, ext, detail) {
        if (this.busy) return;
        if (!this.submitGid) { this.err = "先选一个会话"; return; }
        this.busy = true; this.doneMsg = ""; this.err = "";
        const body = { gid: this.submitGid, ext: ext || "png",
                       brief: this.mtool === "cmp" && this.cmpBrief,
                       detail: detail || "medium" };
        if (this.mtool === "rev") {
          /* 图像反推：参考图 + 可选补充描述（edit） */
          body.image = imageB64 || "";
          body.edit = (this.revFocus || "").trim();
          if (!body.image && !body.edit) { this.busy = false; this.err = "选一张参考图，或写一句要改成什么样"; return; }
        } else {
          /* Agent 补全：描述 + 勾选项拼补充要求 */
          body.what = (this.cmpText || "").trim();
          body.brief = !!this.cmpBrief;
          const MAP = {
            fill: "把空白或过短的框描述补全，保持与整体风格一致",
            cam: "按机位写清可见面和透视关系",
            unify: "统一整体描述与各框的主体、风格、光线，保持一致",
            types: "检查文字框和其它框的类型标注是否正确，修正明显错误",
          };
          const parts = (this.cmpChecks || []).map(k => MAP[k]).filter(Boolean);
          if ((this.cmpExtra || "").trim()) parts.push(this.cmpExtra.trim());
          body.edit = parts.join("；");
          if (!body.what) { this.busy = false; this.err = "先写一句要画什么"; return; }
        }
        this.api("/api/moss_submit", { method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, API),
          body: JSON.stringify(body) }).then(r => {
          this.busy = false;
          if (r.state === "ok") {
            this.doneMsg = (r.data && r.data.message) || "已提交，画好发到所选会话";
          } else {
            this.err = (r.data && r.data.error) || r.error || "提交失败";
          }
        });
      },
      hl(it) {
        if (!this.q) return false;
        const t = ((it.label || "") + " " + (it.desc || "") + " " + (it.key || "")).toLowerCase();
        return t.indexOf(this.q.toLowerCase()) >= 0;
      },
      toast(text, ok) {
        const id = Date.now() + Math.random();
        this.toastQ.push({ id, text, ok: !!ok });
        setTimeout(() => { this.toastQ = this.toastQ.filter(t => t.id !== id); }, 6000);
      },
      async setKnob(it, value) {
        const k = it.key || it.label;
        const r = await this.api("/api/config", { method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, API),
          body: JSON.stringify({ key: k, value: value }) });
        if (r.state === "ok") {
          delete this.dirty[k];           /* 保存成功：清 dirty（修订③） */
          this.toast((it.label || k) + " → " + value, true);
          this.load();
        } else {
          /* 保存失败：toast 红 + 输入保留原样（仍 dirty），不回滚文本 */
          this.toast("没改成：" + (r.data && r.data.message || r.error || "?"), false);
        }
      },
      /* 拉取：GET /api/custom_models[?vl=1] → 清单点选 → prefix 拼接 → 原 key 保存（修订④契约） */
      async pull(it) {
        this.mpick = { on: true, title: (it.picker_vl ? "选择视觉模型" : "选择模型") + " · " + (it.label || ""),
                       cnt: "请求中…", models: [], target: it };
        const vl = it.picker_vl ? "?vl=1" : "";
        const r = await this.api("/api/custom_models" + vl);
        if (r.state !== "ok") {
          /* 失败：弹窗内红字显示，不关窗不写值（修订④错误处理） */
          this.mpick.cnt = "拉取失败：" + r.error;
          return;
        }
        const ms = r.data.models || [];
        this.mpick.models = ms;
        this.mpick.cnt = ms.length ? ("共 " + ms.length + " 个，点一个选用") : "该站没返回模型清单";
      },
      useModel(m) {
        const it = this.mpick.target;
        this.mpick.on = false;
        if (it) this.setKnob(it, (it.picker_prefix || "") + m);
      },
      /* 测速：GET /api/probe_model?key=…；字段名以 _probe_stream 实际返回为准
         （verdict=人话结论主展示，advice=建议，chat.first/sec=数字细节） */
      async probe(it) {
        const key = it.key || it.label;
        this.probing[key] = true;
        this.toast("测速中：" + (it.value || "（未选）") + " …", true);
        try {
          const r = await this.api("/api/probe_model?key=" + encodeURIComponent(key));
          const d = r.data || {};
          if (d.ok && d.verdict) {
            this.toast("【测速】" + d.verdict + (d.advice ? " ｜ 建议：" + d.advice : ""), true);
          } else if (d.ok && d.chat) {
            this.toast("【测速】" + (d.model || "") + "：首字 " + (d.chat.first != null ? d.chat.first + "s" : "—")
              + " · 总 " + (d.chat.sec != null ? d.chat.sec + "s" : "—"), true);
          } else {
            this.toast("测速失败：" + (d.error || r.error || "?"), false);
          }
        } finally { this.probing[key] = false; }
      },
      guardLeave(action) {
        /* 修订③：切分类前查 dirty；放弃才丢 */
        if (this.dirtyCount > 0 &&
            !confirm("系统设置有 " + this.dirtyCount + " 项改动未保存。\n「确定」放弃这些改动并" + action +
                     "，「取消」留在本页。")) return false;
        this.dirty = {};
        return true;
      },
      pickCat(g) { if (g !== this.cat && this.guardLeave("切换分类")) this.cat = g; }
    },
    mounted() {
      /* 深链分类（#/ability/云端 Moss）：自己读 hash，不依赖父组件传值（子组件 mounted 早于父组件 sync） */
      try {
        const raw = String(location.hash || "");
        const seg = decodeURIComponent(raw.replace(/^#\/?/, "").split("/")[1] || "");
        if (seg) this.pendingCat = seg;
      } catch (e) {}
      this.load();
      this._bu = e => { if (this.dirtyCount > 0) { e.preventDefault(); e.returnValue = ""; } };
      window.addEventListener("beforeunload", this._bu);
    },
    unmounted() { window.removeEventListener("beforeunload", this._bu); },
    template: `
    <div>
      <div class="wsbar">
        <input class="field wsbar__q" type="search" placeholder="搜本页配置：名称 / 说明 / key"
          aria-label="搜索配置项" v-model="q">
        <span class="wsbar__n" v-if="q">{{ totalHit }} 项命中（命中高级参数会自动展开）</span>
        <span class="wsbar__n wsbar__n--dirty" v-if="dirtyCount">{{ dirtyCount }} 项改动未保存</span>
      </div>
      <div class="ws">
        <nav class="wsnav">
          <button v-for="c in cats" :key="c.group" type="button" class="wsnav__b"
            :class="{ 'wsnav__b--on': cat === c.group }" @click="pickCat(c.group)">
            <span>{{ c.name }}</span><span class="wsnav__n">{{ c.n }}</span>
          </button>
        </nav>
        <section class="wsbody" v-if="state === 'ok' && rowGroups.length">
          <div v-for="rg in rowGroups" :key="rg.title" class="wsgroup"
            :class="{ 'wsgroup--block': catDefs == null && rg.title === '大脑' }">
            <div class="wsgroup__head">
              <div>
                <h2 class="card__title">{{ rg.title }}</h2>
                <p class="wsdesc">{{ curDesc }}</p>
              </div>
              <span class="card__meta">注册 {{ rg.g.items.length }} · 当前可见 {{ rg.vis.length }} · 改动即时生效</span>
            </div>
            <div class="wsentry" v-if="catDefs == null && cat === '人格'">
              人格的查看与编辑在
              <a href="#/persona">「人格中心」→</a>
              （这里只列人格相关的行为开关）
            </div>
            <div class="wsrow" v-for="it in rg.rows" :key="it.key || it.label"
              :class="{ 'wsrow--hl': hl(it), 'wsrow--dirty': isDirty(it) }" :title="it.desc || ''">
              <div class="wsrow__lab">
                <div class="wsrow__labline">
                  <span class="wsrow__name">{{ it.label }}</span>
                  <span class="tag" v-if="it.apply === 'restart'">要重启</span>
                  <span class="tag" v-if="it.writable === false || it.type === 'secret'">只读</span>
                  <span class="tag tag--dirty" v-if="isDirty(it)">未保存</span>
                </div>
                <span class="wsrow__desc" v-if="desc1(it)" :title="it.desc || ''">{{ desc1(it) }}</span>
              </div>
              <div class="wsrow__ctl">
                <span class="ro" v-if="it.writable === false || it.type === 'secret'">{{ it.mask ? (it.value ? "已配置" : "未配置") : it.display }}</span>
                <template v-else-if="it.type === 'bool'">
                  <c-switch :on="!!it.value" :label="it.label" @toggle="setKnob(it, it.value ? '关' : '开')"></c-switch>
                </template>
                <template v-else-if="it.type === 'enum'">
                  <div class="seg" v-if="(it.values || []).length <= 4">
                    <button v-for="v in it.values" :key="v" type="button" class="seg__b"
                      :class="{ 'seg__b--on': String(it.value) === String(v) }"
                      @click="setKnob(it, v)">{{ v }}</button>
                  </div>
                  <select v-else class="field wsrow__select" :value="it.value"
                    @change="setKnob(it, $event.target.value)">
                    <option v-for="v in it.values" :key="v" :value="v">{{ v }}</option>
                    <option v-if="it.value != null && (it.values || []).indexOf(it.value) < 0"
                      :value="it.value">{{ it.value }}（当前）</option>
                  </select>
                </template>
                <template v-else-if="it.type === 'int' || it.type === 'float' || it.type === 'str'">
                  <input class="field wsrow__input" :class="{ 'field--dirty': isDirty(it) }" type="text"
                    :id="'ws_' + (it.key || it.label)" :value="dirtyOf(it)"
                    @input="onInput(it, $event)">
                  <button class="retry" :class="{ 'retry--save': isDirty(it) }" type="button"
                    @click="setKnob(it, dirtyOf(it))">保存</button>
                </template>
                <span v-else class="ro">{{ it.display }}</span>
                <button v-if="it.picker" class="retry" type="button" @click="pull(it)">拉取</button>
                <button v-if="it.picker" class="retry" type="button"
                  :disabled="probing[it.key || it.label]"
                  @click="probe(it)">{{ probing[it.key || it.label] ? "测速中…" : "测速" }}</button>
              </div>
            </div>
            <p class="pnote" v-if="!rg.rows.length && !rg.adv.length && q">「{{ rg.title }}」没有匹配「{{ q }}」的配置。</p>
            <div class="mossTools" v-if="rg.g && rg.g.group === '云图moss'">
              <button type="button" class="mossTool" @click="openMTool('rev')">
                <b>图像反推提示词</b>
                <span>上传一张参考图 → 视觉模型反推画面元素 → 生成 Moss 提示词（同 Studio「图像反推」）</span>
              </button>
              <button type="button" class="mossTool" @click="openMTool('cmp')">
                <b>Agent 补全</b>
                <span>写一句要画什么 → 出草稿；加补充要求交给 AI 补全/改稿（同 Studio「Agent 补全」）</span>
              </button>
            </div>
            <details class="foldadv" v-if="rg.adv.length" :open="!!q">
              <summary>高级参数（{{ rg.adv.length }}）<span v-if="q">· 已按搜索展开</span></summary>
              <div class="wsfold">
                <div class="wsrow" v-for="it in rg.adv" :key="it.key || it.label"
                  :class="{ 'wsrow--hl': hl(it), 'wsrow--dirty': isDirty(it) }" :title="it.desc || ''">
                  <div class="wsrow__lab"><span class="wsrow__name">{{ it.label }}</span>
                    <span class="tag" v-if="it.apply === 'restart'">要重启</span>
                    <span class="tag tag--dirty" v-if="isDirty(it)">未保存</span></div>
                  <div class="wsrow__ctl">
                    <input class="field wsrow__input" :class="{ 'field--dirty': isDirty(it) }" type="text"
                      :id="'ws_' + (it.key || it.label)" :value="dirtyOf(it)"
                      @input="onInput(it, $event)">
                    <button class="retry" :class="{ 'retry--save': isDirty(it) }" type="button"
                      @click="setKnob(it, dirtyOf(it))">保存</button>
                  </div>
                </div>
              </div>
            </details>
          </div>
        </section>
        <div class="empty" v-else-if="state === 'loading'"><b>读取配置中…</b></div>
        <div class="empty" v-else-if="state === 'down'"><b>服务不可用</b>{{ error }}
          <div><button class="retry" type="button" @click="load">重试</button></div></div>
      </div>
      <!-- Moss 工具·图像反推（提交给 bot） -->
      <div class="tokbox" v-if="mtool === 'rev'">
        <div class="tokbox__card mtool__card">
          <b>图像反推 → 让 bot 画</b>
          <p>发一张参考图，bot 走 Studio 链路：视觉反推定位框 → 云端出图 → <b>画好发到所选会话</b>（和群里「moss反推」同一实现）。</p>
          <div class="mtool__ck" v-for="g in sessions" :key="g.group_id">
            <input type="radio" name="msess" :value="String(g.group_id)" v-model="submitGid">
            {{ g.name }}（{{ g.group_id }}）
          </div>
          <div class="seg">
            <button v-for="d in [['long','详细'],['medium','适中'],['short','简洁']]" :key="d[0]"
              type="button" class="seg__b" :class="{ 'seg__b--on': revDetail === d[0] }"
              @click="revDetail = d[0]">{{ d[1] }}</button>
          </div>
          <input class="field" type="text" v-model="revFocus" placeholder="补充要求（可选），如：让她举起伞、换成雨夜">
          <div class="tokbox__row">
            <input class="field" type="file" accept="image/*" :disabled="busy" @change="doSubmitReve">
          </div>
          <div v-if="busy" class="cstate cstate--pending"><span class="cstate__dot"></span><span>已选图，提交中…</span></div>
          <div v-if="doneMsg" class="cstate cstate--ok"><span class="cstate__dot"></span><span>{{ doneMsg }}（云端约 20~40 秒出图）</span>
            <button class="retry" type="button" @click="mtool = ''">关闭</button></div>
          <div v-if="err" class="cstate cstate--down"><span class="cstate__dot"></span><span>{{ err }}</span></div>
          <div class="tokbox__row" v-if="!doneMsg">
            <button class="retry" type="button" @click="mtool = ''">取消</button>
          </div>
        </div>
      </div>
      <!-- Moss 工具·Agent 补全（提交给 bot） -->
      <div class="tokbox" v-if="mtool === 'cmp'">
        <div class="tokbox__card mtool__card">
          <b>Agent 补全 → 让 bot 画</b>
          <p>写一句要画什么，勾选补全项交给 AI；bot 走 Studio 提示词工程 → 云端出图 → <b>发到所选会话</b>。</p>
          <div class="mtool__ck" v-for="g in sessions" :key="g.group_id">
            <input type="radio" name="msess2" :value="String(g.group_id)" v-model="submitGid">
            {{ g.name }}（{{ g.group_id }}）
          </div>
          <textarea class="field mtool__out" style="min-height:70px" v-model="cmpText"
            placeholder="要画什么，如：雨夜霓虹街头，少女撑伞回头"></textarea>
          <label class="mtool__ck" v-for="c in [['fill','补全空白 / 过短的描述'],['unify','统一整体描述与各框（主体/风格/光线）'],['types','检查文字框和框的类型'],['cam','按机位写清可见面与透视']]" :key="c[0]">
            <input type="checkbox" :value="c[0]" v-model="cmpChecks"> {{ c[1] }}
          </label>
          <label class="mtool__ck"><input type="checkbox" v-model="cmpBrief"> 简易档（4~6 个框，快一半）</label>
          <input class="field" type="text" v-model="cmpExtra" placeholder="补充要求（可选），如：整体更压抑、改成雨夜">
          <div v-if="err" class="cstate cstate--down"><span class="cstate__dot"></span><span>{{ err }}</span></div>
          <div class="tokbox__row">
            <button class="btn" type="button" :disabled="busy" @click="doSubmit()">{{ busy ? "提交中…" : "确认，发给 bot 画" }}</button>
            <button class="retry" type="button" @click="mtool = ''">取消</button>
          </div>
          <div v-if="doneMsg" class="cstate cstate--ok"><span class="cstate__dot"></span><span>{{ doneMsg }}（云端约 20~40 秒出图）</span></div>
        </div>
      </div>
      <div class="tokbox" v-if="mpick.on">
        <div class="tokbox__card mpick__card">
          <b>{{ mpick.title }}</b>
          <p>{{ mpick.cnt }}</p>
          <div class="mpick__list">
            <button v-for="m in mpick.models" :key="m" type="button" class="mpick__m"
              @click="useModel(m)">{{ m }}</button>
          </div>
          <div class="tokbox__row"><button class="retry" type="button" @click="mpick.on = false">取消</button></div>
        </div>
      </div>
      <div class="toasts">
        <div v-for="t in toastQ" :key="t.id" class="toast" :class="t.ok ? 'toast--ok' : 'toast--err'">{{ t.text }}</div>
      </div>
    </div>`
  };

  /* —— 会话中心（批2b五）：会话总控表 / 消息记录 / 白名单 —— */
  const SessionsPage = {
    components: { CSwitch },
    data() {
      return {
        state: "loading", error: "", tab: "ctl",
        sc: null, groups: [], gid: "", hist: null, histLoading: false,
        wl: null, wlAdd: "",
        busy: {}, toastQ: []
      };
    },
    computed: {
      curGroups() { return this.groups; }
    },
    methods: {
      async api(path, opts) {
        try {
          const r = await fetch(path, Object.assign({ headers: API }, opts || {}));
          if (r.status === 401) { showTokbox(true); return { state: "down", auth: true, error: "401" }; }
          if (!r.ok) return { state: "down", error: "HTTP " + r.status };
          const d = await r.json();
          return d && d.ok !== false ? { state: "ok", data: d } : { state: "down", error: d.error || "返回异常", data: d };
        } catch (e) { return { state: "down", error: String(e && e.message || e) }; }
      },
      toast(text, ok) {
        const id = Date.now() + Math.random();
        this.toastQ.push({ id, text, ok: !!ok });
        setTimeout(() => { this.toastQ = this.toastQ.filter(t => t.id !== id); }, 5000);
      },
      async load() {
        this.state = "loading";
        const [sc, g, wl] = await Promise.all([
          this.api("/api/session_control", { method: "POST",
            headers: Object.assign({ "Content-Type": "application/json" }, API),
            body: JSON.stringify({}) }),
          this.api("/api/groups"), this.api("/api/whitelist")]);
        this.sc = sc.state === "ok" ? sc.data : null;
        if (sc.state !== "ok") { this.state = "down"; this.error = sc.error; return; }
        this.groups = (g.state === "ok" && g.data.groups) || [];
        this.wl = wl.state === "ok" ? wl.data : null;
        if (!this.gid && this.groups.length) this.gid = String(this.groups[0].group_id);
        this.state = "ok";
        if (this.tab === "hist") this.loadHist();
      },
      async loadHist() {
        this.histLoading = true;
        this.hist = await this.api("/api/history?group=" + this.gid + "&limit=60");
        this.histLoading = false;
      },
      pickTab(t) { this.tab = t; if (t === "hist" && !this.hist) this.loadHist(); },
      pickGroup(gid) { this.gid = String(gid); if (this.tab === "hist") this.loadHist(); },
      async scPost(body, label) {
        const r = await this.api("/api/session_control", { method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, API),
          body: JSON.stringify(body) });
        this.toast(r.state === "ok" ? (label + " 已生效") : "没改成：" + (r.data && r.data.error || r.error || "?"), r.state === "ok");
        if (r.state === "ok") {
          const sc = await this.api("/api/session_control", { method: "POST",
            headers: Object.assign({ "Content-Type": "application/json" }, API),
            body: JSON.stringify({}) });
          this.sc = sc.state === "ok" ? sc.data : this.sc;
        }
      },
      flip(row, field, label) {
        const k = row.gid + ":" + field;
        if (this.busy[k]) return;
        this.busy[k] = true;
        this.scPost({ gid: row.gid, field: field, value: row[field] ? "关" : "开" },
          label + (row[field] ? " 关" : " 开")).finally(() => { this.busy[k] = false; });
      },
      setPersona(row, ev) {
        this.scPost({ gid: row.gid, field: "persona", value: ev.target.value }, "人格");
      },
      setEngine(pref) { this.scPost({ gid: 0, engine_pref: pref }, "生图引擎 → " + pref); },
      async addWl() {
        const q = (this.wlAdd || "").trim();
        if (!q) return;
        const r = await this.api("/api/whitelist_add", { method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, API),
          body: JSON.stringify({ qq: q }) });
        this.toast(r.state === "ok" ? "已加入白名单" : "没加上：" + (r.data && r.data.error || "?"), r.state === "ok");
        if (r.state === "ok") { this.wlAdd = ""; this.wl = await this.api("/api/whitelist"); this.wl = this.wl.data; }
      },
      async delWl(qq) {
        if (!confirm("把 " + qq + " 移出白名单？")) return;
        const r = await this.api("/api/whitelist_remove", { method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, API),
          body: JSON.stringify({ qq: qq }) });
        this.toast(r.state === "ok" ? "已移出" : "没移出：" + (r.data && r.data.error || "?"), r.state === "ok");
        if (r.state === "ok") this.wl = (await this.api("/api/whitelist")).data;
      },
      ago(sec) {
        if (sec == null) return "";
        if (sec < 60) return sec + " 秒前";
        if (sec < 3600) return Math.round(sec / 60) + " 分钟前";
        return Math.round(sec / 3600) + " 小时前";
      }
    },
    mounted() { this.load(); },
    template: `
    <div>
      <div class="wsbar"><span class="wsbar__hint">会话总控的每个格子即时生效；消息记录为内存快照（bot 重启后从零积累）。</span></div>
      <div class="ws">
        <nav class="wsnav">
          <button v-for="t in [['ctl','会话总控'],['hist','消息记录'],['wl','白名单']]" :key="t[0]"
            type="button" class="wsnav__b" :class="{ 'wsnav__b--on': tab === t[0] }" @click="pickTab(t[0])">
            <span>{{ t[1] }}</span>
          </button>
        </nav>
        <section class="wsbody" v-if="state === 'ok'">
          <!-- 会话总控 -->
          <template v-if="tab === 'ctl' && sc">
            <div class="wsgroup wsgroup--block">
              <div class="wsgroup__head">
                <div><h2 class="card__title">生图引擎</h2>
                <p class="wsdesc">两条画图链路的默认选择（各会话可在自己行里覆盖）</p></div>
                <div class="seg">
                  <button type="button" class="seg__b" :class="{ 'seg__b--on': sc.engine_pref === 'comfyui' }"
                    @click="setEngine('comfyui')">ComfyUI</button>
                  <button type="button" class="seg__b" :class="{ 'seg__b--on': sc.engine_pref === 'moss' }"
                    @click="setEngine('moss')">Moss</button>
                </div>
              </div>
            </div>
            <div class="wsgroup wsgroup--block">
              <div class="wsgroup__head"><div><h2 class="card__title">会话总控</h2>
                <p class="wsdesc">每个群聊 / 私聊独立的响应、插话、画图、Moss、本地工具与人格</p></div></div>
              <div class="sctable">
                <div class="sctable__head">
                  <span>会话</span><span>响应</span><span>插话</span><span>ComfyUI</span><span>Moss</span><span>工具</span><span>人格</span>
                </div>
                <div class="sctable__row" v-for="row in (sc.rows || [])" :key="row.gid">
                  <span class="sctable__name">
                    {{ row.name }}{{ row.private ? "（私聊）" : "" }}
                    <i class="sctable__sub" v-if="!row.private">{{ row.members || 0 }} 人 · {{ (sc.engine_pref === 'moss' ? 'moss' : 'comfyui') }}</i>
                  </span>
                  <span v-for="f in [['ai','响应'],['sim','插话'],['comfyui','ComfyUI'],['moss','Moss'],['tools','工具']]" :key="row.gid + f[0]">
                    <c-switch :on="!!row[f[0]]" :label="row.name + ' ' + f[1]"
                      @toggle="flip(row, f[0], f[1])"></c-switch>
                  </span>
                  <span>
                    <select class="field wsrow__select" :value="row.persona || ''"
                      @change="setPersona(row, $event)">
                      <option value="">跟随全局</option>
                      <option v-for="p in (sc.personas || [])" :key="p" :value="p">{{ p }}</option>
                    </select>
                  </span>
                </div>
              </div>
            </div>
          </template>
          <!-- 消息记录 -->
          <template v-if="tab === 'hist'">
            <div class="wsgroup wsgroup--block">
              <div class="wsgroup__head">
                <div><h2 class="card__title">消息记录</h2>
                <p class="wsdesc">内存收件箱快照（重启后从零积累），最多回看 60 条</p></div>
                <div class="seg">
                  <button v-for="g in curGroups" :key="g.group_id" type="button" class="seg__b"
                    :class="{ 'seg__b--on': String(g.group_id) === gid }"
                    @click="pickGroup(g.group_id)">{{ g.name }}</button>
                  <button type="button" class="seg__b" @click="loadHist">刷新</button>
                </div>
              </div>
              <div class="wsfold" v-if="hist && hist.state === 'ok'">
                <div v-for="m in (hist.data.messages || []).slice().reverse()" :key="m.id" class="histrow">
                  <span class="histrow__who" :class="{ 'histrow__who--out': m.out }">{{ m.out ? "我方" : m.who }}</span>
                  <span class="histrow__text">{{ m.text || "（图/表情，未转文字）" }}</span>
                  <span class="histrow__ago">{{ ago(m.ago_sec) }}</span>
                </div>
                <div v-if="!(hist.data.messages || []).length" class="empty" style="padding:20px">
                  <b>该群暂无记录</b>加入监控后的新消息才会进来。</div>
              </div>
              <div v-else-if="histLoading" class="sk" style="height:80px"></div>
            </div>
          </template>
          <!-- 白名单 -->
          <template v-if="tab === 'wl'">
            <div class="wsgroup wsgroup--block">
              <div class="wsgroup__head">
                <div><h2 class="card__title">成员白名单</h2>
                <p class="wsdesc">主人与受信任成员（能用管理级旋钮的名单）</p></div>
              </div>
              <div class="wladd">
                <input class="field" type="text" v-model="wlAdd" placeholder="QQ 号" @keyup.enter="addWl">
                <button class="btn" type="button" @click="addWl">加入</button>
              </div>
              <div class="wsrow" v-for="w in ((wl || {}).list || [])" :key="w.qq">
                <div class="wsrow__lab">
                  <div class="wsrow__labline"><span class="wsrow__name">{{ w.name || w.qq }}</span>
                    <span class="tag">{{ w.qq }}</span></div>
                </div>
                <div class="wsrow__ctl"><button class="retry" type="button" @click="delWl(w.qq)">移出</button></div>
              </div>
            </div>
          </template>
        </section>
        <div class="empty" v-else-if="state === 'loading'"><b>读取中…</b></div>
        <div class="empty" v-else-if="state === 'down'"><b>服务不可用</b>{{ error }}
          <div><button class="retry" type="button" @click="load">重试</button></div></div>
      </div>
      <div class="toasts">
        <div v-for="t in toastQ" :key="t.id" class="toast" :class="t.ok ? 'toast--ok' : 'toast--err'">{{ t.text }}</div>
      </div>
    </div>`
  };

  /* —— 插件与扩展（批2b五）：已安装插件 / 插件市场 —— */
  const PluginsPage = {
    data() {
      return { state: "loading", error: "", cfg: null, market: null,
               cat: "已安装", busy: {}, openSkill: "", toastQ: [],
               /* meme 服务卡（2026-11-05）：状态/体检/启动/重启；mm = /api/meme 结果 */
               mm: null, mmBusy: false, mmPoll: 0,
               /* 模板库（2026-11-05 晚）：目录/搜索/增删；只点开时才拉（300+ 项） */
               cat: null, catOpen: false, catQ: "", catBusy: false, catErr: "", catAdding: "" };
    },
    computed: {
      plugGroup() { return (this.cfg || []).find(g => g.group === "插件列表") || null; },
      plugins() { return (this.plugGroup || {}).plugins || []; },
      marketItems() { return ((this.market || {}).data || {}).items || []; },
      mmSvcOk() {
        return !!(this.mm && this.mm.state === "ok" && this.mm.data.service && this.mm.data.service.ok);
      },
      mmEnabled() {
        return (this.mm && this.mm.state === "ok" && this.mm.data.templates) || [];
      },
      catItems() {
        const q = (this.catQ || "").trim().toLowerCase();
        const items = (this.cat && this.cat.state === "ok" && this.cat.data.items) || [];
        if (!q) return items;
        return items.filter(it =>
          ((it.key || "") + " " + (it.keywords || []).join(" ")).toLowerCase().indexOf(q) >= 0);
      },
      skills() {
        /* 插件参数按 skill 分组（/api/config 插件列表组 items 的 skill 字段） */
        const out = {};
        ((this.plugGroup || {}).items || []).forEach(it => {
          if (!it.skill) return;
          (out[it.skill] = out[it.skill] || []).push(it);
        });
        return out;
      }
    },
    methods: {
      async api(path, opts) {
        try {
          const r = await fetch(path, Object.assign({ headers: API }, opts || {}));
          if (r.status === 401) { showTokbox(true); return { state: "down", auth: true, error: "401" }; }
          if (!r.ok) return { state: "down", error: "HTTP " + r.status };
          const d = await r.json();
          return d && d.ok !== false ? { state: "ok", data: d } : { state: "down", error: d.error || "返回异常", data: d };
        } catch (e) { return { state: "down", error: String(e && e.message || e) }; }
      },
      toast(text, ok) {
        const id = Date.now() + Math.random();
        this.toastQ.push({ id, text, ok: !!ok });
        setTimeout(() => { this.toastQ = this.toastQ.filter(t => t.id !== id); }, 5000);
      },
      async load() {
        this.state = "loading";
        const [c, m, mm] = await Promise.all([this.api("/api/config"), this.api("/api/market?op=list"),
                                              this.api("/api/meme")]);
        if (c.state !== "ok") { this.state = "down"; this.error = c.error; return; }
        this.cfg = c.data.groups || [];
        this.market = m;
        this.mm = mm;          /* meme 状态拉不到不挡整页，卡片里自己显示离线 */
        this.state = "ok";
      },
      async setKnob(it, value) {
        const k = it.key || it.label;
        const r = await this.api("/api/config", { method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, API),
          body: JSON.stringify({ key: k, value: value }) });
        this.toast(r.state === "ok" ? ((it.label || k) + " → " + value) :
          "没改成：" + (r.data && r.data.message || r.error || "?"), r.state === "ok");
        if (r.state === "ok") this.load();
      },
      async marketOp(op, id) {
        if (op === "uninstall" && !confirm("卸载插件「" + id + "」？它的工具与参数会一并停用（可随时重装）。")) return;
        this.busy[id] = true;
        const r = await this.api("/api/market", { method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, API),
          body: JSON.stringify({ op: op, id: id }) });
        this.busy[id] = false;
        this.toast(r.state === "ok" ? ((op === "install" ? "已安装 " : "已卸载 ") + id) :
          "没成功：" + (r.data && r.data.error || "?"), r.state === "ok");
        if (r.state === "ok") this.load();
      },
      toggleOpen(id) { this.openSkill = this.openSkill === id ? "" : id; },
      /* meme 服务操作（2026-11-05）：health/start/restart 都转调 bot 侧 action_meme_ctl，
         防误杀判定在 _meme_lab/meme_ctl.py，前端只管把结果说清楚 + 启动后轮询到就绪。 */
      async mmCtl(op) {
        if (this.mmBusy) return;
        this.mmBusy = true;
        const r = await this.api("/api/meme", { method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, API),
          body: JSON.stringify({ op: op }) });
        this.mmBusy = false;
        if (r.state !== "ok") { this.toast("meme 服务操作失败：" + (r.error || "?"), false); return; }
        const d = r.data || {};
        if (op === "health") {
          const okc = d.rc === 0;
          this.toast(okc ? "meme 服务健康" :
            "health 没过（rc=" + d.rc + (d.output ? "）：" + String(d.output).slice(-80) : "）"), okc);
        } else {
          this.toast((op === "start" ? "启动" : "重启") + "指令已发" + (d.ok ? "，成功" :
            "，rc=" + d.rc + (d.output ? "：" + String(d.output).slice(-70) : "")), !!d.ok);
          this.mmPollStart();
        }
      },
      mmPollStart() {
        clearInterval(this.mmPoll);
        let n = 0;
        this.mmPoll = setInterval(async () => {
          n++;
          const r = await this.api("/api/meme");
          if (r.state === "ok") this.mm = r;
          const okc = r.state === "ok" && r.data.service && r.data.service.ok;
          if (okc || n >= 12) {
            clearInterval(this.mmPoll);
            if (!okc) this.toast("等了 36 秒服务还没就绪，去「运行监控」看日志。", false);
            else this.toast("meme 服务已就绪", true);
          }
        }, 3000);
      },
      /* meme 模板库（2026-11-05 晚）：预览 / 开抽屉 / 添加 / 移除 */
      pv(key) { return "/api/meme_preview?key=" + encodeURIComponent(key) + "&token=" + encodeURIComponent(TOKEN); },
      fmtReq(pt) {
        const parts = [];
        if (pt.min_images > 0) parts.push("图 " + pt.min_images + (pt.max_images > pt.min_images ? "~" + pt.max_images : ""));
        if (pt.min_texts > 0) parts.push("字 " + pt.min_texts + (pt.max_texts > pt.min_texts ? "~" + pt.max_texts : ""));
        return parts.join(" + ") || "无参数";
      },
      async openCat() {
        this.catOpen = true;
        if (this.cat && this.cat.state === "ok") return;
        this.catBusy = true; this.catErr = "";
        this.cat = await this.api("/api/meme_catalog");
        this.catBusy = false;
        if (this.cat.state !== "ok") this.catErr = this.cat.error || "取不到";
      },
      async catOp(it) {
        if (this.catAdding) return;
        this.catAdding = it.key;
        const op = it.on ? "remove" : "add";
        const r = await this.api("/api/meme_templates", { method: "POST",
          headers: Object.assign({ "Content-Type": "application/json" }, API),
          body: JSON.stringify({ op: op, key: it.key }) });
        this.catAdding = "";
        if (r.state !== "ok" || !(r.data || {}).ok) {
          this.toast("没成功：" + ((r.data && r.data.error) || r.error || "?"), false);
          return;
        }
        this.toast(op === "add" ? "已启用 " + it.key + "，在「参数与开关」里改口令" : "已移除 " + it.key, true);
        /* 卡片元信息/旋钮/服务状态都要跟上 */
        this.load();
        this.cat = null;      /* 目录里的 on 标记变了，重开时重新拉 */
        this.openCat();
      }
    },
    beforeUnmount() { clearInterval(this.mmPoll); },
    mounted() { this.load(); },
    template: `
    <div>
      <div class="ws"><nav class="wsnav">
          <button type="button" class="wsnav__b" :class="{ 'wsnav__b--on': cat === '已安装' }" @click="cat = '已安装'">
            <span>已安装</span><span class="wsnav__n">{{ plugins.length }}</span></button>
          <button type="button" class="wsnav__b" :class="{ 'wsnav__b--on': cat === '市场' }" @click="cat = '市场'">
            <span>插件市场</span><span class="wsnav__n">{{ marketItems.length }}</span></button>
        </nav>
        <section class="wsbody" v-if="state === 'ok'">
          <!-- 已安装 -->
          <template v-if="cat === '已安装'">
            <div class="pluggrid">
            <div v-for="p in plugins" :key="p.id" class="wsgroup wsgroup--block plugcard">
              <div class="wsgroup__head">
                <div>
                  <h2 class="card__title">{{ p.name }} <i class="plugcard__id">{{ p.id }}</i></h2>
                  <p class="wsdesc">{{ p.desc || "（没有简介）" }}</p>
                </div>
                <span class="card__meta">{{ p.native ? "原生工具" : "提示词包" }}<br>工具 {{ (p.tools || []).length }} 项</span>
              </div>
              <!-- meme 服务块（2026-11-05）：状态 + 体检/启动/重启，参数走下面的通用折叠 -->
              <div class="memesvc" v-if="p.meme">
                <div class="kv">
                  <span class="kv__k">服务状态</span>
                  <span class="kv__v" :class="mmSvcOk ? 'kv__v--ok' : 'kv__v--warn'">
                    {{ mmSvcOk ? "在线 · " + mm.data.service.keys + " 个模板" : (mm && mm.state === "ok" ? "离线" : "…") }}</span>
                  <span class="kv__k" v-if="mmSvcOk">路由</span>
                  <span class="kv__v" v-if="mmSvcOk">{{ mm.data.service.routes }}</span>
                  <span class="kv__k">地址</span>
                  <span class="kv__v">{{ (mm && mm.state === 'ok' && mm.data.api_url) || "—" }}</span>
                  <span class="kv__k">总开关</span>
                  <span class="kv__v" :class="p.on ? 'kv__v--ok' : 'kv__v--warn'">{{ p.on ? "开" : "关" }}</span>
                </div>
                <p class="wsdesc" v-if="mm && mm.state === 'ok' && !mmSvcOk && mm.data.service && mm.data.service.err">
                  探测失败：{{ mm.data.service.err }}</p>
                <div class="memesvc__btns">
                  <button class="retry" type="button" :disabled="mmBusy" @click="mmCtl('health')">体检</button>
                  <button class="retry" type="button" :disabled="mmBusy" @click="mmCtl('start')">启动服务</button>
                  <button class="retry" type="button" :disabled="mmBusy" @click="mmCtl('restart')">重启服务</button>
                  <span style="flex:1"></span>
                  <button class="retry retry--save" type="button" @click="openCat">模板库（{{ mm.state === 'ok' ? mm.data.service.keys : "…" }}）</button>
                </div>
                <!-- 已启用模板预览：玩法 = 口令 + 图来源（本条图 / @人头像 / 双人头像） -->
                <div class="memesvc__prev" v-if="mmEnabled.length">
                  <figure v-for="t in mmEnabled" :key="t.key">
                    <img :src="pv(t.key)" loading="lazy" :alt="t.key">
                    <figcaption><b>{{ t.keywords.join("、") }}</b><span>{{ t.key }}</span></figcaption>
                  </figure>
                </div>
                <p class="wsdesc" v-if="mmEnabled.length">玩法：发图+口令 = 用这张图；「口令 @某人」= 单人模板用 TA 的头像、双人模板用你俩的头像（你是主动方）；什么都不带 = 单人模板用你自己的头像。</p>
                <p class="wsdesc">服务本体在 _meme_lab（3.12 venv），keeper_guard 每轮自动守护；这里启动/重启走 meme_ctl.py，误杀防护在管理器里。</p>
              </div>
              <details class="foldadv" v-if="skills[p.id]" :open="openSkill === p.id">
                <summary @click.prevent="toggleOpen(p.id)">参数与开关（{{ skills[p.id].length }}）</summary>
                <div class="wsfold">
                <div class="wsrow" v-for="it in skills[p.id]" :key="it.key || it.label" :title="it.desc || ''">
                  <div class="wsrow__lab">
                    <div class="wsrow__labline"><span class="wsrow__name">{{ it.label }}</span>
                      <span class="tag" v-if="it.apply === 'restart'">要重启</span></div>
                    <span class="wsrow__desc" v-if="it.desc">{{ it.desc.split(/[。！？]/)[0] }}</span>
                  </div>
                  <div class="wsrow__ctl">
                    <template v-if="it.type === 'bool' && it.writable !== false">
                      <c-switch :on="!!it.value" :label="it.label" @toggle="setKnob(it, it.value ? '关' : '开')"></c-switch>
                    </template>
                    <template v-else-if="it.type === 'enum' && it.writable !== false">
                      <select class="field wsrow__select" :value="it.value" @change="setKnob(it, $event.target.value)">
                        <option v-for="v in (it.values || [])" :key="v" :value="v">{{ v }}</option>
                      </select>
                    </template>
                    <template v-else-if="it.writable !== false && it.type !== 'secret'">
                      <input class="field wsrow__input" type="text" :id="'pg_' + (it.key || it.label)"
                        :value="it.value == null ? '' : it.value"
                        @keyup.enter="setKnob(it, $event.target.value)">
                      <button class="retry" type="button"
                        @click="setKnob(it, document.getElementById('pg_' + (it.key || it.label)).value)">保存</button>
                    </template>
                    <span v-else class="ro">{{ it.display }}</span>
                  </div>
                </div>
                </div>
              </details>
            </div>
            </div>
            <div class="empty" v-if="!plugins.length"><b>还没有已安装的插件</b>去「插件市场」装一个。</div>
            <p class="pnote">AstrBot 官方目录是只读浏览（那些是 NoneBot 框架的插件，本机装不了），完整目录页在旧 /admin。</p>
          </template>
          <!-- 市场 -->
          <template v-if="cat === '市场'">
            <div class="pluggrid">
            <div v-for="m in marketItems" :key="m.id" class="wsgroup wsgroup--block plugcard">
              <div class="wsgroup__head">
                <div>
                  <h2 class="card__title">{{ m.name }}</h2>
                  <p class="wsdesc">{{ m.summary || "（没有简介）" }}</p>
                </div>
                <span class="card__meta">{{ m.author || "未知" }}<br>v{{ m.version || "?" }}</span>
              </div>
              <div class="plugcard__foot">
                <span class="tag" v-for="t in (m.tags || []).slice(0, 3)" :key="t">{{ t }}</span>
                <span class="plugcard__foot__sp"></span>
                <button v-if="!m.installed" class="retry retry--save" type="button"
                  :disabled="busy[m.id]" @click="marketOp('install', m.id)">
                  {{ busy[m.id] ? "安装中…" : "安装" }}</button>
                <button v-else class="retry" type="button"
                  :disabled="busy[m.id]" @click="marketOp('uninstall', m.id)">
                  {{ busy[m.id] ? "卸载中…" : "卸载" }}</button>
              </div>
            </div>
            </div>
          </template>
        </section>
        <div class="empty" v-else-if="state === 'loading'"><b>读取中…</b></div>
        <div class="empty" v-else-if="state === 'down'"><b>服务不可用</b>{{ error }}
          <div><button class="retry" type="button" @click="load">重试</button></div></div>
      </div>
      <div class="toasts">
        <div v-for="t in toastQ" :key="t.id" class="toast" :class="t.ok ? 'toast--ok' : 'toast--err'">{{ t.text }}</div>
      </div>
      <!-- meme 模板库弹层（2026-11-05 晚）：全部可用模板 + 预览 + 一键启用/移除 -->
      <div class="mmodal" v-if="catOpen" @click.self="catOpen = false">
        <div class="mmodal__box">
          <div class="mmodal__head">
            <b>meme 模板库</b>
            <span class="mmodal__cnt" v-if="cat && cat.state === 'ok'">{{ catItems.length }} / {{ cat.data.count }} 个</span>
            <input class="field mmodal__q" type="text" v-model="catQ" placeholder="搜 key 或关键词…">
            <button class="retry" type="button" @click="catOpen = false">关闭</button>
          </div>
          <div class="mmodal__body">
            <div v-if="catBusy" class="empty"><b>读取中…</b>正在拉全部模板的参数（第一次约 1~2 秒）。</div>
            <div v-else-if="catErr" class="empty"><b>取不到模板目录</b>{{ catErr }}
              <div><button class="retry" type="button" @click="cat = null; openCat()">重试</button></div></div>
            <div class="mmodal__grid" v-else>
              <div v-for="it in catItems" :key="it.key" class="mmodal__cell" :class="{ 'mmodal__cell--on': it.on }">
                <img :src="pv(it.key)" loading="lazy" :alt="it.key">
                <div class="mmodal__meta">
                  <b>{{ it.key }}</b>
                  <span>{{ fmtReq(it) }}</span>
                  <span class="mmodal__kws" v-if="(it.keywords || []).length">{{ it.keywords.join("、") }}</span>
                </div>
                <button v-if="!it.on" class="retry retry--save" type="button"
                  :disabled="catAdding === it.key" @click="catOp(it)">
                  {{ catAdding === it.key ? "…" : "启用" }}</button>
                <button v-else class="retry" type="button"
                  :disabled="catAdding === it.key" @click="catOp(it)">
                  {{ catAdding === it.key ? "…" : "移除" }}</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>`
  };

  /* —— 运行监控（阶段 3）：日志（级别/来源过滤）+ 任务与产物 + 用量排行 —— */
  const MonitoringPage = {
    data() {
      return {
        state: "loading", error: "", tab: "logs",
        logs: null, logSrc: "bot", logLvl: "", logLoading: false,
        mt: null, arts: null, artsLoading: false,
        toastQ: []
      };
    },
    computed: {
      byGroup() {
        /* metrics.msg 里按 gid 聚合排行（近 7 天） */
        const agg = {};
        ((this.mt || {}).data || {}).msg ? this.mt.data.msg.forEach(m => {
          agg[m.gid] = agg[m.gid] || { gid: m.gid, recv: 0, sent: 0 };
          agg[m.gid].recv += m.recv;
          agg[m.gid].sent += m.sent;
        }) : null;
        return Object.values(agg).sort((a, b) => (b.recv + b.sent) - (a.recv + a.sent)).slice(0, 10);
      }
    },
    methods: {
      async api(path, opts) {
        try {
          const r = await fetch(path, Object.assign({ headers: API }, opts || {}));
          if (r.status === 401) { showTokbox(true); return { state: "down", auth: true, error: "401" }; }
          if (!r.ok) return { state: "down", error: "HTTP " + r.status };
          const d = await r.json();
          return d && d.ok !== false ? { state: "ok", data: d } : { state: "down", error: d.error || "返回异常", data: d };
        } catch (e) { return { state: "down", error: String(e && e.message || e) }; }
      },
      toast(text, ok) {
        const id = Date.now() + Math.random();
        this.toastQ.push({ id, text, ok: !!ok });
        setTimeout(() => { this.toastQ = this.toastQ.filter(t => t.id !== id); }, 5000);
      },
      async load() {
        this.state = "loading";
        const mt = await this.api("/api/metrics?hours=168");
        this.mt = mt;
        this.state = "ok";
        if (this.tab === "logs") this.loadLogs();
      },
      async loadLogs() {
        this.logLoading = true;
        this.logs = await this.api("/api/logs?source=" + this.logSrc +
          "&level=" + this.logLvl + "&tail=300");
        this.logLoading = false;
      },
      pickLog(src, lvl) {
        if (src != null) this.logSrc = src;
        if (lvl != null) this.logLvl = lvl;
        this.loadLogs();
      },
      async loadArts() { this.artsLoading = true; this.arts = await this.api("/api/artifacts?limit=14"); this.artsLoading = false; },
      pickTab(t) { this.tab = t; if (t === "arts" && !this.arts) this.loadArts(); },
      ago(sec) {
        if (sec == null) return "—";
        if (sec < 60) return sec + " 秒前";
        if (sec < 3600) return Math.round(sec / 60) + " 分钟前";
        if (sec < 86400) return Math.round(sec / 3600) + " 小时前";
        return Math.round(sec / 86400) + " 天前";
      }
    },
    mounted() { this.load(); },
    template: `
    <div>
      <div class="wsbar">
        <span class="wsbar__hint">日志只读、默认最新在前；产物为 images/ 目录最近文件。</span>
      </div>
      <div class="ws">
        <nav class="wsnav">
          <button type="button" class="wsnav__b" :class="{ 'wsnav__b--on': tab === 'logs' }" @click="pickTab('logs')">
            <span>日志</span></button>
          <button type="button" class="wsnav__b" :class="{ 'wsnav__b--on': tab === 'arts' }" @click="pickTab('arts')">
            <span>任务与产物</span></button>
          <button type="button" class="wsnav__b" :class="{ 'wsnav__b--on': tab === 'rank' }" @click="pickTab('rank')">
            <span>用量排行</span></button>
        </nav>
        <section class="wsbody" v-if="state === 'ok'">
          <!-- 日志 -->
          <template v-if="tab === 'logs'">
            <div class="wsgroup wsgroup--block">
              <div class="wsgroup__head">
                <div><h2 class="card__title">bot 日志</h2>
                <p class="wsdesc">文件尾部 512KB，最新在前；点来源/级别过滤后点「应用」刷新</p></div>
              </div>
              <div class="seg" style="margin-bottom:8px">
                <button type="button" class="seg__b" :class="{ 'seg__b--on': logSrc === 'bot' }" @click="pickLog('bot', null)">运行日志</button>
                <button type="button" class="seg__b" :class="{ 'seg__b--on': logSrc === 'err' }" @click="pickLog('err', null)">错误日志</button>
                <span style="width:12px"></span>
                <button v-for="lv in ['', 'INFO', 'WARNING', 'ERROR']" :key="lv || 'all'" type="button" class="seg__b"
                  :class="{ 'seg__b--on': logLvl === lv }" @click="pickLog(null, lv)">{{ lv || '全部' }}</button>
                <button type="button" class="retry" style="margin-left:8px" @click="loadLogs">应用刷新</button>
              </div>
              <div class="logbox" v-if="logs && logs.state === 'ok'">
                <div v-for="(ln, i) in (logs.data.lines || [])" :key="i" class="logline"
                  :class="{ 'logline--err': ln.indexOf(' ERROR ') >= 0, 'logline--warn': ln.indexOf(' WARNING ') >= 0 }">{{ ln }}</div>
                <div v-if="!(logs.data.lines || []).length" class="empty" style="padding:16px">
                  <b>没有匹配的日志行</b>{{ logs.data.note || "" }}</div>
              </div>
              <div v-else-if="logLoading" class="sk" style="height:120px"></div>
            </div>
          </template>
          <!-- 任务与产物 -->
          <template v-if="tab === 'arts'">
            <div class="grid">
              <c-card title="ComfyUI 队列" meta="实时" :state="arts && arts.state === 'ok' ? 'ok' : (arts && arts.state === 'down' ? 'down' : 'loading')"
                span="span6" error="读取失败" @retry="loadArts">
                <div class="kv" v-if="arts && arts.state === 'ok' && arts.data.comfy">
                  <span class="kv__k">状态</span>
                  <span class="kv__v" :class="arts.data.comfy.server_up ? 'kv__v--ok' : 'kv__v--warn'">{{ arts.data.comfy.server_up ? "在线" : "离线" }}</span>
                  <span class="kv__k">渲染中</span><span class="kv__v">{{ arts.data.comfy.drawing ? "是" : "否" }}</span>
                  <span class="kv__k">排队</span><span class="kv__v">{{ arts.data.comfy.queued == null ? "—" : arts.data.comfy.queued }}</span>
                  <span class="kv__k">本小时</span><span class="kv__v">{{ arts.data.comfy.done_this_hour == null ? "—" : arts.data.comfy.done_this_hour }} / {{ arts.data.comfy.max_per_hour == null ? "—" : arts.data.comfy.max_per_hour }}</span>
                </div>
              </c-card>
              <c-card title="最近产物" meta="images/ 按时间倒序" :state="arts && arts.state === 'ok' ? 'ok' : (arts && arts.state === 'down' ? 'down' : 'loading')"
                span="span6" error="读取失败" @retry="loadArts">
                <div v-if="arts && arts.state === 'ok'">
                  <div v-for="a in (arts.data.items || [])" :key="a.name" class="artrow">
                    <span class="artrow__name">{{ a.name }}</span>
                    <span class="artrow__meta">{{ a.kb }} KB · {{ ago(a.ago_sec) }}</span>
                  </div>
                  <div v-if="!(arts.data.items || []).length" class="empty" style="padding:16px"><b>还没有产物</b></div>
                </div>
              </c-card>
            </div>
          </template>
          <!-- 用量排行 -->
          <template v-if="tab === 'rank'">
            <c-card title="各会话消息量" meta="近 7 天" :state="mt && mt.state === 'ok' ? 'ok' : (mt && mt.state === 'down' ? 'down' : 'loading')"
              span="span8" error="读取失败" @retry="load">
              <div v-if="mt && mt.state === 'ok' && byGroup.length">
                <div v-for="r in byGroup" :key="r.gid" class="wsrow">
                  <div class="wsrow__lab"><span class="wsrow__name">{{ r.gid > 0 ? ("群 " + r.gid) : ("私聊 " + (-r.gid)) }}</span></div>
                  <div class="wsrow__ctl"><span class="ro">收 {{ r.recv }} · 发 {{ r.sent }}</span></div>
                </div>
              </div>
              <div v-else-if="mt && mt.state === 'ok'" class="cstate cstate--pending">
                <span class="cstate__dot"></span><span>计数器刚上线，还没有按会话的累计数据。</span></div>
            </c-card>
          </template>
        </section>
        <div class="empty" v-else-if="state === 'loading'"><b>读取中…</b></div>
        <div class="empty" v-else-if="state === 'down'"><b>服务不可用</b>{{ error }}
          <div><button class="retry" type="button" @click="load">重试</button></div></div>
      </div>
      <div class="toasts">
        <div v-for="t in toastQ" :key="t.id" class="toast" :class="t.ok ? 'toast--ok' : 'toast--err'">{{ t.text }}</div>
      </div>
    </div>`
  };

  /* —— 侧栏与路由（hash：#/dashboard 等）—— */
  const PAGES = [
    { key: "dashboard", name: "总览", done: true },
    { key: "sessions", name: "会话中心", done: true, page: "sessions" },
    { key: "persona", name: "人格中心", done: true },
    { key: "behavior", name: "行为与主动性", done: true, page: "settings",
      catsDef: [{ name: "行为开关", groups: ["群友"] },
                { name: "黑话", groups: ["黑话"] },
                { name: "表达", groups: ["表达"] },
                { name: "时机", groups: ["时机"] },
                { name: "会话服务", groups: ["群务"] }] },
    { key: "ability", name: "AI 能力", done: true, page: "settings",
      catsDef: [{ name: "现画", groups: ["现画"] },
                { name: "图库", groups: ["图库"] },
                { name: "工作流选择", groups: ["工作流选择"] },
                { name: "反推与画同款", groups: ["反推"] },
                { name: "云端 Moss", groups: ["云图moss"] },
                { name: "视频解析", groups: ["视频"] },
                { name: "视频生成", groups: ["视频生成"] }] },
    { key: "plugins", name: "插件与扩展", done: true, page: "plugins" },
    { key: "monitor", name: "运行监控", done: true, page: "monitor" },
    { key: "system", name: "系统设置", done: true, page: "settings" }
  ];

  const App = {
    components: { Dashboard, Placeholder, PersonaPage, KnobsPage, SettingsPage, SessionsPage, PluginsPage, MonitoringPage },
    data() { return { pages: PAGES, route: "dashboard", sub: "", tokenMissing: !TOKEN, store }; },
    computed: {
      current() { return this.pages.find(p => p.key === this.route) || this.pages[0]; },
      curPage() { return this.pages.find(p => p.key === this.route) || null; }
    },
    methods: {
      go(key) { location.hash = "#/" + key; },
      sync() {
        const parts = (location.hash || "").replace(/^#\/?/, "").split("/");
        const key = parts[0] || "dashboard";
        const next = this.pages.some(p => p.key === key) ? key : "dashboard";
        /* 修订③：系统设置有未保存改动时，离开本页需明确放弃 */
        if (next !== this.route && store.settingsDirty > 0 && this.route === "system") {
          if (!confirm("系统设置有 " + store.settingsDirty + " 项改动未保存。\n「确定」放弃并离开，「取消」留在本页。")) {
            try { history.replaceState(null, "", "#/" + this.route); } catch (e) {}
            return;
          }
          store.settingsDirty = 0;   /* 明确放弃 */
        }
        this.route = next;
        /* 页内二级深链（#/persona/reflect、#/ability/云端 Moss）——中文分类名要先解码 */
        let sub = parts[1] || "";
        try { sub = decodeURIComponent(sub); } catch (e) {}
        this.sub = sub;
      }
    },
    mounted() {
      this.sync();
      window.addEventListener("hashchange", this.sync);
    },
    template: `
    <div class="shell">
      <aside class="rail">
        <div class="brand">
          <span class="brand__mark">Q</span>
          <span><b class="brand__name">qq-bot</b><span class="brand__sub">控制中心</span></span>
        </div>
        <div class="railhead">分区</div>
        <nav class="nav">
          <button v-for="p in pages" :key="p.key" type="button" class="nav__btn"
            :class="{ 'nav__btn--wip': !p.done }"
            :aria-current="route === p.key ? 'page' : 'false'" @click="go(p.key)">
            <span>{{ p.name }}</span>
            <span class="nav__n" v-if="!p.done" title="该页在后续阶段开放">未开放</span>
          </button>
        </nav>
        <div class="railfoot">
          新控制台（阶段 1 骨架）。旧 /admin 保留可用；两者共用同一套 API 与密钥。
        </div>
      </aside>

      <main class="main">
        <header class="topbar">
          <div>
            <h1 class="topbar__title">{{ current.name }}</h1>
            <p class="topbar__sub" v-if="tokenMissing">未带 token：URL 加 ?token= 或填入 localStorage.qqbot_token 后刷新。</p>
            <p class="topbar__sub" v-else>改完即时生效；历史趋势类数据在计数器上线前显示「累计中」。</p>
          </div>
          <div class="topbar__right">
            <span class="conn" :class="'conn--' + store.conn">
              <span class="conn__dot"></span>{{ store.conn === 'on' ? '在线' : (store.conn === 'off' ? '离线' : '…') }}</span>
          </div>
        </header>
        <div class="content">
          <dashboard v-if="route === 'dashboard'" ref="view"></dashboard>
          <persona-page v-else-if="route === 'persona'" ref="view" :initial-tab="sub"></persona-page>
          <settings-page v-else-if="curPage && curPage.page === 'settings'" ref="view"
            :cat-defs="curPage.catsDef || null"></settings-page>
          <sessions-page v-else-if="curPage && curPage.page === 'sessions'" ref="view"></sessions-page>
          <plugins-page v-else-if="curPage && curPage.page === 'plugins'" ref="view"></plugins-page>
          <monitoring-page v-else-if="curPage && curPage.page === 'monitor'" ref="view"></monitoring-page>
          <knobs-page v-else-if="curPage && (curPage.zone || curPage.zones)" ref="view"
            :zone="curPage.zone || ''" :zones="curPage.zones || ''" :intro="curPage.intro || ''"></knobs-page>
          <placeholder v-else :page="current.name" :note="current.note"></placeholder>
        </div>
      </main>
    </div>`
  };

  /* —— token 输入卡（与 /admin 的 tokbox 同语义）：无 token 或 401 时出现 —— */
  function showTokbox(withErr) {
    const box = document.getElementById("tokbox");
    if (!box) return;
    box.hidden = false;
    const err = document.getElementById("tokerr");
    if (err) err.hidden = !withErr;
    const inp = document.getElementById("tokinput");
    if (inp) inp.focus();
  }
  function initTokbox() {
    const box = document.getElementById("tokbox");
    if (!box) return;
    const submit = function () {
      const inp = document.getElementById("tokinput");
      const v = (inp && inp.value || "").trim();
      if (!v) return;
      try { localStorage.setItem("qqbot_token", v); } catch (e) {}
      location.reload();
    };
    const btn = document.getElementById("tokbtn");
    if (btn) btn.onclick = submit;
    const inp = document.getElementById("tokinput");
    if (inp) inp.addEventListener("keydown", function (e) { if (e.key === "Enter") submit(); });
  }
  initTokbox();
  if (!TOKEN) showTokbox(false);
  const app = Vue.createApp(App);
  app.mount("#app");

  /* 全局 JS 错误 → 页面右上角红色 toast（黑箱排查终结者：主人看到什么发我什么） */
  window.__qqErr = function (e) {
    const msg = (e && (e.message || e.reason && e.reason.message)) || String(e);
    const box = document.getElementById("errtoast");
    if (!box) return;
    box.textContent = "页面脚本错误：" + msg;
    box.hidden = false;
    clearTimeout(window.__qqErrT);
    window.__qqErrT = setTimeout(function () { box.hidden = true; }, 10000);
  };
  window.addEventListener("error", function (ev) { window.__qqErr(ev.error || ev.message); });
  window.addEventListener("unhandledrejection", function (ev) { window.__qqErr(ev.reason); });
})();
