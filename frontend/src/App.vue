<template>
  <div class="app-container" :class="{ 'light-theme': isLightTheme }">
    <div class="mobile-overlay" :class="{ active: sidebarOpen }" @click="sidebarOpen = false"></div>
    <AppSidebar :class="{ 'mobile-open': sidebarOpen }" />
    <main class="main-content">
      <AppHeader @toggle-sidebar="sidebarOpen = !sidebarOpen" />
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <keep-alive>
            <component :is="Component" />
          </keep-alive>
        </transition>
      </router-view>
    </main>
    <SettingsModal @openAuth="openAuthModal" />
    <AuthModal ref="authModal" />
    <SourceDrawer />
    <Toast />
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useSettingsStore } from './stores/settings'
import { useAuthStore } from './stores/auth'
import AppSidebar from './components/AppSidebar.vue'
import AppHeader from './components/AppHeader.vue'
import SettingsModal from './components/SettingsModal.vue'
import AuthModal from './components/AuthModal.vue'
import SourceDrawer from './components/SourceDrawer.vue'
import Toast from './components/Toast.vue'

const settingsStore = useSettingsStore()
const authStore = useAuthStore()
const isLightTheme = computed(() => settingsStore.theme === 'light')
const sidebarOpen = ref(false)
const authModal = ref(null)

// Apply theme class to documentElement for CSS :root selector
watch(isLightTheme, (isLight) => {
  if (isLight) {
    document.documentElement.classList.add('light-theme')
  } else {
    document.documentElement.classList.remove('light-theme')
  }
}, { immediate: true })

function openAuthModal(mode = 'login') {
  authModal.value?.open(mode)
}

// 任意组件（如 ChatView 未登录发消息时）通过 window 事件请求打开登录弹窗，
// 与 'auth-changed' 事件同一套全局通信方式
function onOpenAuth(e) {
  openAuthModal(e.detail?.mode || 'login')
}

// Auto-login check on mount
onMounted(async () => {
  if (authStore.token) {
    const ok = await authStore.checkAuth()
    if (ok) {
      window.dispatchEvent(new CustomEvent('auth-changed'))
    }
  }
})

// ESC 关闭最上层的弹窗/抽屉：向最上层可见的 overlay 派发一次自身点击，
// 复用各组件已有的 @click.self 关闭逻辑，无需逐个改造弹窗组件
function onGlobalKeydown(e) {
  if (e.key !== 'Escape') return
  const overlays = document.querySelectorAll('.modal-overlay.active, .source-drawer-overlay.active')
  if (overlays.length === 0) return
  const topmost = overlays[overlays.length - 1]
  topmost.dispatchEvent(new MouseEvent('click', { bubbles: true }))
}

// 侧边栏（AppSidebar）通过此事件请求关闭移动端菜单，
// 保证 sidebarOpen 状态与 DOM 始终同步（不直接操作 DOM class）
function onCloseMobileSidebar() {
  sidebarOpen.value = false
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKeydown)
  window.addEventListener('close-mobile-sidebar', onCloseMobileSidebar)
  window.addEventListener('open-auth', onOpenAuth)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onGlobalKeydown)
  window.removeEventListener('close-mobile-sidebar', onCloseMobileSidebar)
  window.removeEventListener('open-auth', onOpenAuth)
})
</script>

<style>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
