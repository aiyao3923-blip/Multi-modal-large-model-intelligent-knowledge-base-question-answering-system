<template>
  <div class="page-heading">
    <h1>{{ title }}</h1>
    <div v-if="$slots.default || $slots.actions" class="page-actions">
      <slot name="actions"><slot /></slot>
    </div>
  </div>
</template>

<script setup>
// Legacy callers may still pass these props. Supporting them does not make them a hero block.
defineProps({ title: String, description: String, eyebrow: String })
</script>
