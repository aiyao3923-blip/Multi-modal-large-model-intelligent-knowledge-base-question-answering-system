import { defineStore } from 'pinia'
import { parseJson } from '@/utils/format'
let checking, checkingToken
export const useUserStore = defineStore('user', {
  state: () => ({ token: localStorage.getItem('token') || '', user: parseJson(localStorage.getItem('user'), null), checked: false }),
  getters: { isLogin: (s) => !!s.token && !!s.user, role: (s) => s.user?.role_code || '', isAdmin: (s) => s.user?.role_code === 'admin', isOperator: (s) => ['admin', 'operator'].includes(s.user?.role_code) },
  actions: {
    setLogin(token, user) { this.token = token; this.user = user; this.checked = true; localStorage.setItem('token', token); localStorage.setItem('user', JSON.stringify(user)) },
    logout() { this.token = ''; this.user = null; this.checked = false; localStorage.removeItem('token'); localStorage.removeItem('user') },
    async validateSession() {
      if (!this.token) { this.logout(); return false }
      if (this.checked) return this.isLogin
      const token = this.token
      if (!checking || checkingToken !== token) {
        checkingToken = token
        checking = import('@/api').then(({ meApi }) => meApi()).then((user) => {
          if (this.token !== token) return this.isLogin
          this.setLogin(token, user)
          return true
        }).catch(() => {
          if (this.token === token) this.logout()
          return this.isLogin
        }).finally(() => { if (checkingToken === token) { checking = null; checkingToken = null } })
      }
      return checking
    },
  },
})
