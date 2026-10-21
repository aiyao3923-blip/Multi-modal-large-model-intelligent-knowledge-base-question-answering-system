<template><el-config-provider :locale="zhCn"><a class="skip-link" href="#main-content">跳转到主要内容</a><router-view /></el-config-provider></template><script setup>
import { ElConfigProvider } from 'element-plus'
import 'element-plus/es/components/config-provider/style/css.mjs'
import zhCn from 'element-plus/es/locale/lang/zh-cn'</script><style>.skip-link{position:fixed;top:-60px;left:16px;background:var(--color-surface);border:2px solid var(--color-focus);padding:9px 15px;z-index:9999;border-radius:4px}.skip-link:focus{top:10px}</style>
