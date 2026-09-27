<template>
  <div class="youtube-options">
    <label>可见性<el-select :model-value="modelValue.visibility" aria-label="YouTube 可见性" @update:model-value="change('visibility', $event)"><el-option label="公开" value="public" /><el-option label="不公开（凭链接访问）" value="unlisted" /><el-option label="私享" value="private" /></el-select></label>
    <label>是否面向儿童<el-select :model-value="modelValue.made_for_kids" placeholder="请选择视频受众" aria-label="YouTube 是否面向儿童" @update:model-value="change('made_for_kids', $event)"><el-option label="否，不是面向儿童的内容" :value="false" /><el-option label="是，面向儿童" :value="true" /></el-select></label>
  </div>
</template>
<script setup>
const props = defineProps({ modelValue: { type: Object, required: true } })
const emit = defineEmits(['update:modelValue'])
const change = (key, value) => emit('update:modelValue', { ...props.modelValue, [key]: value })
</script>
<style scoped>
.youtube-options { display: grid; gap: 16px; margin-bottom: 18px; min-width: 0; }
label { display: grid; gap: 8px; font-size: 14px; }
.el-select { width: 100%; min-width: 0; }
</style>
