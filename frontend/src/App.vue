<script setup>
import { ref, reactive, onMounted } from "vue";
import { api, convOf, setConv } from "./api.js";
import DataSourcePanel from "./components/DataSourcePanel.vue";
import ConversationPanel from "./components/ConversationPanel.vue";
import SchemaPanel from "./components/SchemaPanel.vue";
import MetricsPanel from "./components/MetricsPanel.vue";
import ChatPanel from "./components/ChatPanel.vue";

const connections = ref([]);
const currentConn = ref("");
const catalog = ref(null);
const metrics = ref([]);
const conversations = ref([]);
const activeConvId = ref("");
const messages = ref([]);
const question = ref("");
const loading = ref(false);
const showConnModal = ref(false);
const showMetricModal = ref(false);
const connForm = reactive({ name: "", url: "" });
const metricForm = reactive({ name: "", aliases: "", formula: "", table_name: "" });
const llm = reactive({ text: "检测模型状态…", cls: "badge", title: "" });
const examples = [
  "各渠道的销售额是多少",
  "近30天销售额",
  "每月销售额趋势",
  "卖得最好的5个商品",
  "哪个城市的客单价最高",
  "各品类的订单量占比",
  "有哪些表",
];

function note(text) {
  messages.value.push({ type: "note", text });
}

async function loadHealth() {
  try {
    const h = await api("/api/health");
    if (h.llm_available) {
      llm.text = `模型：${h.llm_model} · 点我测连通`;
      llm.cls = "badge on";
      llm.title = h.llm_base_url || "";
    } else {
      llm.text = `未配置模型${h.llm_missing?.length ? "（缺 " + h.llm_missing.join("/") + "）" : ""} · 规则兜底`;
      llm.cls = "badge off";
      llm.title = "在项目根目录 .env 里配置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL，然后重启服务";
    }
  } catch (e) {
    llm.text = "服务未连接";
    llm.cls = "badge off";
  }
}

async function testLLM() {
  const old = llm.text;
  llm.text = "正在请求模型…";
  try {
    const r = await api("/api/llm/test");
    if (r.ok) {
      llm.cls = "badge on";
      llm.text = `模型正常 · ${r.latency_ms}ms`;
    } else {
      llm.cls = "badge off";
      llm.text = "模型不可用 · 点看原因";
      llm.title = r.error || "未知错误";
      alert("模型不可用：" + (r.error || "未知错误"));
    }
  } catch (e) {
    llm.text = "测试失败";
    alert("测试失败：" + e.message);
  }
  setTimeout(() => {
    if (llm.text !== old) loadHealth();
  }, 4000);
}

async function loadSchema() {
  if (!currentConn.value) {
    catalog.value = null;
    return;
  }
  try {
    const data = await api(`/api/connections/${currentConn.value}/schema`);
    catalog.value = data.catalog;
  } catch (e) {
    catalog.value = null;
  }
}

async function loadMetrics() {
  if (!currentConn.value) {
    metrics.value = [];
    return;
  }
  try {
    metrics.value = await api(`/api/metrics/${currentConn.value}`);
  } catch (e) {
    metrics.value = [];
  }
}

async function loadConversations() {
  if (!currentConn.value) {
    conversations.value = [];
    activeConvId.value = "";
    return [];
  }
  try {
    conversations.value = await api(`/api/conversations?connection_id=${encodeURIComponent(currentConn.value)}`);
  } catch (e) {
    conversations.value = [];
  }
  const cur = convOf(currentConn.value);
  if (cur && conversations.value.some((c) => c.id === cur)) activeConvId.value = cur;
  else {
    activeConvId.value = "";
    if (cur) setConv(currentConn.value, "");
  }
  return conversations.value;
}

async function loadConnData(announce) {
  await Promise.all([loadSchema(), loadMetrics(), loadConversations()]);
  if (announce && conversations.value.length) {
    note(`本库有 ${conversations.value.length} 个历史会话，已在左侧展开；点一条会在右侧回放。`);
  }
}

async function loadConnections(selectId) {
  connections.value = await api("/api/connections");
  let pick = selectId || currentConn.value;
  if (!connections.value.some((c) => c.id === pick)) pick = connections.value[0]?.id || "";
  currentConn.value = pick;
  if (!pick) {
    catalog.value = null;
    metrics.value = [];
    conversations.value = [];
    activeConvId.value = "";
    return;
  }
  await loadConnData(false);
}

async function selectConn(id) {
  currentConn.value = id;
  messages.value = [];
  await loadConnData(true);
}

async function refreshSchema() {
  if (!currentConn.value) return;
  try {
    await api(`/api/connections/${currentConn.value}/refresh`, { method: "POST" });
    await loadConnections(currentConn.value);
    note("表结构已刷新。");
  } catch (e) {
    alert(e.message);
  }
}

async function deleteConn() {
  if (!currentConn.value) return;
  if (!confirm("删除当前数据源？它的指标口径和会话记忆会一起删除，且不可恢复。")) return;
  const id = currentConn.value;
  try {
    await api(`/api/connections/${id}`, { method: "DELETE" });
    setConv(id, "");
    messages.value = [];
    await loadConnections();
    note("数据源已删除。");
  } catch (e) {
    alert(e.message);
  }
}

async function addConn() {
  if (!connForm.name.trim() || !connForm.url.trim()) return alert("名称和连接串都要填");
  try {
    const c = await api("/api/connections", {
      method: "POST",
      body: JSON.stringify({ name: connForm.name.trim(), url: connForm.url.trim() }),
    });
    showConnModal.value = false;
    connForm.name = "";
    connForm.url = "";
    messages.value = [];
    await loadConnections(c.id);
    note("数据源已接入，结构已读取。");
  } catch (e) {
    alert(e.message);
  }
}

async function addMetric() {
  if (!metricForm.name.trim() || !metricForm.formula.trim()) return alert("指标名和表达式都要填");
  try {
    await api("/api/metrics", {
      method: "POST",
      body: JSON.stringify({
        connection_id: currentConn.value,
        name: metricForm.name.trim(),
        aliases: metricForm.aliases.trim(),
        formula: metricForm.formula.trim(),
        table_name: metricForm.table_name.trim(),
      }),
    });
    showMetricModal.value = false;
    metricForm.name = "";
    metricForm.aliases = "";
    metricForm.formula = "";
    metricForm.table_name = "";
    await loadMetrics();
    note("口径已保存，会随问题一起送给模型。");
  } catch (e) {
    alert(e.message);
  }
}

async function deleteMetric(id) {
  if (!confirm("删除这条口径？")) return;
  try {
    await api(`/api/metrics/${id}`, { method: "DELETE" });
    await loadMetrics();
  } catch (e) {
    alert(e.message);
  }
}

function turnToAnswer(t) {
  const p = t.payload && typeof t.payload === "object" ? t.payload : {};
  return {
    ...p,
    summary: p.summary || t.summary || "",
    sql: p.sql || t.sql || "",
    columns: p.columns || [],
    rows: p.rows || [],
    row_count: p.row_count ?? t.row_count ?? 0,
    elapsed_ms: p.elapsed_ms ?? t.latency_ms ?? 0,
    used_tables: p.used_tables || t.tables || [],
    error: p.error || t.error || "",
    trace: p.trace || [],
    chart: p.chart || { type: "table", x: null, y: [] },
    created_at: t.created_at,
    payloadFull: !!t.payload,
  };
}

async function openConversation(id) {
  try {
    const data = await api(`/api/conversations/${id}/turns`);
    setConv(currentConn.value, id);
    activeConvId.value = id;
    messages.value = [{ type: "note", text: `历史会话 ${id} · 共 ${data.total} 轮，以下为回放；继续提问会接着它追问` }];
    data.turns.forEach((t) => {
      messages.value.push({ type: "user", text: t.question });
      messages.value.push({ type: "answer", data: turnToAnswer(t), replay: true });
    });
    await loadConversations();
  } catch (e) {
    messages.value.push({ type: "error", text: `读取会话失败：${e.message}` });
  }
}

async function deleteConversation(id) {
  if (!confirm("删除这个会话？服务端保存的全部轮次会一起删除，不可恢复。")) return;
  try {
    await api(`/api/conversations/${id}`, { method: "DELETE" });
    if (activeConvId.value === id) {
      setConv(currentConn.value, "");
      activeConvId.value = "";
      messages.value = [];
      note("当前会话已删除；可以点左侧其他会话继续，或点「新会话」重新开始。");
    }
    await loadConversations();
  } catch (e) {
    alert(e.message);
  }
}

async function newConversation() {
  if (!currentConn.value) return;
  if (!confirm("开启新会话？旧会话会保留在“会话历史”里，随时点回来续接。")) return;
  setConv(currentConn.value, "");
  activeConvId.value = "";
  messages.value = [];
  note("已开启新会话；旧会话都在左侧“会话历史”里，点一下就能续接。");
  await loadConversations();
}

async function ask(text) {
  const q = (typeof text === "string" ? text : question.value).trim();
  if (!q) return;
  if (!currentConn.value) return alert("请先选择或新增一个数据源");
  if (loading.value) return;
  question.value = "";
  loading.value = true;
  messages.value.push({ type: "user", text: q });
  try {
    const res = await api("/api/ask", {
      method: "POST",
      body: JSON.stringify({
        connection_id: currentConn.value,
        question: q,
        conversation_id: activeConvId.value || null,
      }),
    });
    messages.value.push({ type: "answer", data: res });
    if (res.conversation_id) {
      setConv(currentConn.value, res.conversation_id);
      activeConvId.value = res.conversation_id;
    }
    await loadConversations();
  } catch (e) {
    messages.value.push({ type: "error", text: `查询失败：${e.message}` });
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  await loadHealth();
  await loadConnections();
});
</script>

<template>
  <div class="app">
    <header>
      <div class="logo">Q</div>
      <h1>QueryHub <span class="sub">通用问数平台</span></h1>
      <div :class="llm.cls" :title="llm.title" @click="testLLM">{{ llm.text }}</div>
    </header>

    <main>
      <aside>
        <DataSourcePanel :connections="connections" :current-id="currentConn" @select="selectConn"
          @refresh="refreshSchema" @delete="deleteConn" @add="showConnModal = true" />
        <ConversationPanel :conversations="conversations" :active-id="activeConvId" :disabled="!currentConn"
          @open="openConversation" @delete="deleteConversation" @new="newConversation" />
        <SchemaPanel :catalog="catalog" />
        <MetricsPanel :metrics="metrics" :disabled="!currentConn" @delete="deleteMetric"
          @add="showMetricModal = true" />
      </aside>

      <ChatPanel :messages="messages" :loading="loading" :question="question" :examples="examples"
        :connection-id="currentConn" :can-ask="!!currentConn" @ask="ask" @update:question="question = $event" />
    </main>

    <div class="modal-mask" v-if="showConnModal" @click.self="showConnModal = false">
      <div class="modal">
        <h3>新增数据源</h3>
        <div class="field"><label>名称</label><input type="text" v-model="connForm.name" placeholder="如 生产订单库" /></div>
        <div class="field"><label>连接串</label><input type="text" v-model="connForm.url"
            placeholder="sqlite:///data/demo_shop.db" /></div>
        <div class="hint">
          SQLite：sqlite:///data/demo_shop.db<br />
          MySQL：mysql+pymysql://user:pass@host:3306/db<br />
          PostgreSQL：postgresql+psycopg://user:pass@host:5432/db<br />
          SQL Server：mssql+pyodbc://user:pass@host:1433/db?driver=ODBC+Driver+17+for+SQL+Server<br />
          Oracle：oracle+oracledb://user:pass@host:1521/?service_name=ORCL
        </div>
        <div class="modal-actions">
          <button @click="showConnModal = false">取消</button>
          <button class="primary" @click="addConn">连接并读取结构</button>
        </div>
      </div>
    </div>

    <div class="modal-mask" v-if="showMetricModal" @click.self="showMetricModal = false">
      <div class="modal">
        <h3>新增指标口径</h3>
        <div class="field"><label>指标名</label><input type="text" v-model="metricForm.name" placeholder="如 GMV" /></div>
        <div class="field"><label>别名</label><input type="text" v-model="metricForm.aliases"
            placeholder="逗号分隔：销售额,成交额" /></div>
        <div class="field"><label>表达式</label><input type="text" v-model="metricForm.formula"
            placeholder="如 SUM(pay_amount)" /></div>
        <div class="field"><label>主表</label><input type="text" v-model="metricForm.table_name"
            placeholder="如 fact_order" /></div>
        <div class="hint">口径会随问题一起送入模型，避免“销售额到底算不算退款”这类歧义。</div>
        <div class="modal-actions">
          <button @click="showMetricModal = false">取消</button>
          <button class="primary" @click="addMetric">保存口径</button>
        </div>
      </div>
    </div>
  </div>
</template>
