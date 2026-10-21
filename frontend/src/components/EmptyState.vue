<template>
  <div class="empty-state">
    <div class="empty-symbol" aria-hidden="true"><AppIcon :name="icon" :size="22" /></div>
    <h3>{{ title }}</h3>
    <p v-if="description">{{ description }}</p>
    <div v-if="$slots.default" class="empty-actions"><slot /></div>
  </div>
</template>

<script setup>
defineProps({
  title: { type: String, default: '暂无内容' },
  description: String,
  icon: { type: String, default: 'folder' },
})
</script>
