<script setup>
defineProps({
  connections: { type: Array, default: () => [] },
  currentId: { type: String, default: "" },
});
defineEmits(["select", "refresh", "delete", "add"]);
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <h2>数据源</h2>
      <span class="pill">{{ connections.length }}</span>
    </div>
    <select :value="currentId" @change="$emit('select', $event.target.value)">
      <option v-if="!connections.length" value="">暂无数据源</option>
      <option v-for="c in connections" :key="c.id" :value="c.id">
        {{ c.name }} · {{ c.dialect }} · {{ c.table_count }}表
      </option>
    </select>
    <div class="data-actions">
      <button @click="$emit('refresh')" :disabled="!currentId">刷新结构</button>
      <button class="danger" @click="$emit('delete')" :disabled="!currentId">删除数据源</button>
    </div>
    <div class="hint" style="margin-top:6px">上面两个按钮只作用于当前选中的数据源</div>
    <button class="ghost wide" @click="$emit('add')">＋ 新增数据源</button>
  </section>
</template>
