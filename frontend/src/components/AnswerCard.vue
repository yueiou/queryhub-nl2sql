<script setup>
import { ref, computed, nextTick, onMounted, onBeforeUnmount, watch } from "vue";
import * as echarts from "echarts";
import { api, copyText } from "../api.js";

const props = defineProps({
  res: { type: Object, required: true },
  connectionId: { type: String, default: "" },
  replay: { type: Boolean, default: false },
});

const chartEl = ref(null);
const fb = ref("");
const copied = ref("");
const openSql = ref(props.replay);
let chart = null;

const summaryText = computed(() => {
  if (props.res.summary) return props.res.summary;
  if (props.res.error) return "查询失败：" + props.res.error;
  return "（没有摘要）";
});

const metaText = computed(() => {
  const r = props.res || {};
  const bits = [];
  if (r.answer_type !== "schema") bits.push(`${r.row_count ?? 0} 行`);
  if (r.elapsed_ms) bits.push(`${r.elapsed_ms} ms`);
  if (r.used_tables?.length) bits.push(`表：${r.used_tables.join("、")}`);
  if (r.context_used) bits.push(`继承上文 ${r.context_turns} 轮`);
  if (r.truncated) bits.push("结果已截断");
  if (props.replay && r.created_at) bits.push(r.created_at);
  return bits.join(" · ");
});

function fmt(v) {
  if (typeof v === "number") return Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
  return v ?? "";
}

function drawChart() {
  const el = chartEl.value;
  if (!el) return;
  const rows = props.res.rows || [];
  if (!rows.length) return;
  const hint = props.res.chart || {};
  const columns = props.res.columns || [];
  if (!hint || hint.type === "table" || !hint.x) {
    el.style.display = "none";
    return;
  }
  const xi = columns.indexOf(hint.x);
  const yIdx = (hint.y || []).map((c) => columns.indexOf(c)).filter((i) => i >= 0);
  if (xi < 0 || !yIdx.length) {
    el.style.display = "none";
    return;
  }
  el.style.display = "block";
  if (chart) {
    chart.dispose();
    chart = null;
  }
  chart = echarts.init(el);
  chart.setOption({
    tooltip: { trigger: "axis" },
    grid: { left: 10, right: 18, top: 30, bottom: 10, containLabel: true },
    xAxis: { type: "category", data: rows.map((r) => r[xi]), axisLabel: { color: "#6b7280" } },
    yAxis: { type: "value", axisLabel: { color: "#6b7280" }, splitLine: { lineStyle: { color: "#f1f5f9" } } },
    series: yIdx.map((yi, k) => ({
      name: columns[yi],
      type: hint.type === "line" ? "line" : "bar",
      data: rows.map((r) => Number(r[yi]) || 0),
      smooth: hint.type === "line",
      barMaxWidth: 28,
      itemStyle: {
        borderRadius: hint.type === "line" ? 0 : [4, 4, 0, 0],
        color: ["#2563eb", "#38bdf8", "#22c55e"][k % 3],
      },
    })),
  });
}

function onResize() {
  if (chart) chart.resize();
}

onMounted(() => {
  nextTick(drawChart);
  window.addEventListener("resize", onResize);
});

onBeforeUnmount(() => {
  window.removeEventListener("resize", onResize);
  if (chart) chart.dispose();
});

watch(() => props.res, () => nextTick(drawChart), { deep: true });

function exportCsv() {
  if (!props.res.log_id) return;
  window.open(`/api/export/${props.res.log_id}?connection_id=${encodeURIComponent(props.connectionId)}`, "_blank");
}

async function feedback(rating) {
  try {
    await api("/api/feedback", {
      method: "POST",
      body: JSON.stringify({ log_id: props.res.log_id, rating }),
    });
    fb.value = rating;
  } catch (e) {
    alert(e.message);
  }
}

async function copy(kind) {
  const text = kind === "sql" ? props.res.sql : summaryText.value;
  const ok = await copyText(text);
  if (!ok) return alert("复制失败，请手动选中文本复制");
  copied.value = kind;
  setTimeout(() => {
    if (copied.value === kind) copied.value = "";
  }, 1600);
}
</script>

<template>
  <div class="card">
    <div class="cardtools">
      <button @click="copy('answer')">{{ copied === 'answer' ? '已复制' : '复制' }}</button>
    </div>
    <div class="warnbar" v-if="res.warning">模型没有生效，本次用的是规则兜底：{{ res.warning }}</div>
    <div class="summary">{{ summaryText }}</div>
    <div class="meta" v-if="metaText">{{ metaText }}</div>
    <div class="meta" v-if="res.analysis">口径说明：{{ res.analysis }}</div>
    <div class="hint" v-if="replay && res.payloadFull === false">
      这一轮早于“完整记录”升级，只保留了问题、摘要和 SQL，结果明细无法补回。
    </div>
    <div class="chart" v-if="res.rows && res.rows.length" ref="chartEl"></div>
    <div class="tablewrap" v-if="res.rows && res.rows.length">
      <table>
        <thead>
          <tr>
            <th v-for="c in res.columns" :key="c">{{ c }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, ri) in res.rows" :key="ri">
            <td v-for="(v, ci) in row" :key="ci" :class="{ num: typeof v === 'number' }">{{ fmt(v) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="exportbar" v-if="res.truncated && res.log_id">
      <button @click="exportCsv">导出当时的完整结果（CSV）</button>
      <small>页面只展示前 {{ res.row_count }} 行；导出会按当时的 SQL 重新取全量。</small>
    </div>
    <details class="sql" v-if="res.sql" :open="openSql">
      <summary>查看生成的 SQL</summary>
      <pre>{{ res.sql }}
      <button v-if="res.sql" @click="copy('sql')">{{ copied === 'sql' ? '已复制' : '复制' }}</button></pre>
      <div class="trace" v-if="res.trace && res.trace.length">
        <div class="step" v-for="(s, si) in res.trace" :key="si">
          <span class="dot" :class="{ warn: s.status === 'warn', err: s.status === 'err' }"></span>
          <span class="tname">{{ s.node }}</span><span>{{ s.detail }}</span><span class="ms">{{ s.ms || 0 }}ms</span>
        </div>
      </div>
    </details>
    <div class="fb" v-if="!replay && res.log_id">
      <template v-if="!fb">这个回答有用吗？<button @click="feedback('up')">有用</button><button
          @click="feedback('down')">不准</button></template>
      <span v-else>{{ fb === 'up' ? '已标记有用' : '已标记不准' }}</span>
    </div>
  </div>
</template>
