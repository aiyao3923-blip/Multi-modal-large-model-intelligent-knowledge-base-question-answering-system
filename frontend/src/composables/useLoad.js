import { ref } from 'vue'
export function useLoad(loader) {
  const loading = ref(false), error = ref('')
  let version = 0
  async function load(...args) { const current = ++version; loading.value = true; error.value = ''; try { return await loader(...args) } catch (e) { if (current === version) error.value = e?.response?.data?.detail || '加载失败，请检查服务连接后重试'; return undefined } finally { if (current === version) loading.value = false } }
  return { loading, error, load }
}
