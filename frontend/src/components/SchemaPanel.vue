<script setup>
defineProps({
  catalog: { type: Object, default: null },
});
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <h2>表结构</h2>
      <span class="pill" v-if="catalog">{{ catalog.tables.length }}</span>
    </div>
    <div class="tree" v-if="catalog && catalog.tables.length">
      <div class="t" v-for="t in catalog.tables" :key="t.name">
        <b>{{ t.name }}</b><em>{{ t.comment || '' }}</em>
        <ul>
          <li v-for="col in t.columns" :key="col.name">
            {{ col.name }} <span style="color:#9ca3af">{{ col.type || '' }}</span><span v-if="col.comment"> · {{ col.comment }}</span>
          </li>
        </ul>
      </div>
    </div>
    <div class="hint" v-else>暂无结构，选数据源或点“刷新结构”。</div>
  </section>
</template>
