<script setup>
defineProps({
  metrics: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
});
defineEmits(["delete", "add"]);
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <h2>指标口径</h2>
      <span class="pill">{{ metrics.length }}</span>
    </div>
    <div class="metric" v-for="m in metrics" :key="m.id">
      <span class="del" @click="$emit('delete', m.id)">删除</span>
      <b>{{ m.name }}</b>
      <small>{{ m.formula }} · {{ m.table_name || '未指定主表' }}</small>
      <small v-if="m.aliases">别名：{{ m.aliases }}</small>
    </div>
    <div class="hint" v-if="!metrics.length">尚未配置口径。</div>
    <button class="ghost wide" @click="$emit('add')" :disabled="disabled">＋ 新增口径</button>
  </section>
</template>
