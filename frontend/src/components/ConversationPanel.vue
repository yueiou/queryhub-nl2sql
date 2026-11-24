<script setup>
defineProps({
  conversations: { type: Array, default: () => [] },
  activeId: { type: String, default: "" },
  disabled: { type: Boolean, default: false },
});
defineEmits(["open", "delete", "new"]);
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <h2>会话历史</h2>
      <div>
        <span class="pill">{{ conversations.length }}</span>
        <button class="mini primary" style="margin-left:6px" @click="$emit('new')" :disabled="disabled">＋ 新会话</button>
      </div>
    </div>
    <div class="hint">默认展开。点任意一条会在右侧完整回放，并接着它继续问。</div>
    <div class="convlist">
      <div
        v-for="c in conversations"
        :key="c.id"
        class="convitem"
        :class="{ active: c.id === activeId }"
        role="button"
        tabindex="0"
        @click="$emit('open', c.id)"
        @keydown.enter.prevent="$emit('open', c.id)"
      >
        <b>{{ c.title || '（无标题）' }}</b>
        <small>{{ c.updated_at }} · {{ c.turn_count }} 轮 <em v-if="c.id === activeId">当前会话</em></small>
        <span class="delconv" @click.stop="$emit('delete', c.id)" title="删除该会话">删除</span>
      </div>
      <div class="hint" v-if="!conversations.length">{{ disabled ? '先选一个数据源。' : '本库还没有会话，问一句就会自动建。' }}</div>
    </div>
  </section>
</template>
