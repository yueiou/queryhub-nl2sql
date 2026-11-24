<script setup>
import { ref, watch, nextTick } from "vue";
import AnswerCard from "./AnswerCard.vue";

const props = defineProps({
  messages: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  question: { type: String, default: "" },
  examples: { type: Array, default: () => [] },
  connectionId: { type: String, default: "" },
  canAsk: { type: Boolean, default: false },
});
defineEmits(["ask", "update:question"]);

const messagesEl = ref(null);

function scrollBottom() {
  nextTick(() => {
    const el = messagesEl.value;
    if (el) el.scrollTop = el.scrollHeight;
  });
}

watch(() => props.messages.length, scrollBottom);
watch(() => props.loading, scrollBottom);
</script>

<template>
  <section class="chat">
    <div class="messages" ref="messagesEl">
      <div class="empty" v-if="!messages.length && !loading">
        选一个数据源，直接用中文提问。<br />
        支持“各渠道销售额”“近30天趋势”“卖得最好的5个商品”这类问法。
      </div>
      <template v-for="(m, idx) in messages" :key="idx">
        <div class="sysnote" v-if="m.type === 'note'">{{ m.text }}</div>
        <div class="msg user" v-else-if="m.type === 'user'"><div>{{ m.text }}</div></div>
        <div class="card" v-else-if="m.type === 'error'"><div class="errtext">{{ m.text }}</div></div>
        <AnswerCard v-else :res="m.data" :connection-id="connectionId" :replay="!!m.replay" />
      </template>
      <div class="card" v-if="loading">
        <div class="loading"><i></i><i></i><i></i> 正在召回表结构、生成并校验 SQL…</div>
      </div>
    </div>

    <div class="composer">
      <div class="chips">
        <span class="chip" v-for="e in examples" :key="e" @click="$emit('ask', e)">{{ e }}</span>
      </div>
      <div class="inputrow">
        <textarea
          :value="question"
          placeholder="例如：最近 7 天各渠道的订单数，按订单量降序"
          @input="$emit('update:question', $event.target.value)"
          @keydown.enter.exact.prevent="$emit('ask', null)"
        ></textarea>
        <button class="primary send" :disabled="loading || !canAsk" @click="$emit('ask', null)">查询</button>
      </div>
    </div>
  </section>
</template>
