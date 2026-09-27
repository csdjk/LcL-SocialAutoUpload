<template>
  <div id="app" :class="{ 'desktop-app': isDesktop }">
    <el-container>
      <el-aside :width="isCollapse ? '64px' : '200px'" :class="{ 'is-collapsed': isCollapse }">
        <div class="sidebar">
          <div class="logo" data-window-drag @dblclick="toggleWindowSize">
            <img class="logo-icon" src="/publisher.svg" alt="" aria-hidden="true" />
            <h2 v-show="!isCollapse">视频发布工作台</h2>
          </div>
          <el-menu
            :router="true"
            :default-active="activeMenu"
            :collapse="isCollapse"
            class="sidebar-menu"
          >
            <el-menu-item index="/">
              <el-icon><HomeFilled /></el-icon>
              <span>今日待发布</span>
            </el-menu-item>
            <el-menu-item index="/content">
              <el-icon><VideoCamera /></el-icon>
              <span>内容库</span>
            </el-menu-item>
            <el-menu-item index="/publish-center">
              <el-icon><Upload /></el-icon>
              <span>任务中心</span>
            </el-menu-item>
            <el-menu-item index="/account-management">
              <el-icon><User /></el-icon>
              <span>账号管理</span>
            </el-menu-item>
            <el-menu-item index="/settings">
              <el-icon><Clock /></el-icon>
              <span>设置</span>
            </el-menu-item>
          </el-menu>
        </div>
      </el-aside>
      <el-container>
        <el-header>
          <div class="header-content" data-window-drag @dblclick.self="toggleWindowSize">
            <div class="header-left">
              <button class="toggle-sidebar" type="button" :aria-label="isCollapse ? '展开侧栏' : '收起侧栏'" :aria-expanded="!isCollapse" @click="toggleSidebar"><el-icon><Fold /></el-icon></button>
              <el-button class="mobile-menu" text @click="mobileNav = true">菜单</el-button>
              <span class="current-page" @dblclick="toggleWindowSize">{{ pageTitle }}</span>
            </div>
            <div class="header-right">
              <button class="theme-toggle" type="button" :aria-label="theme === 'light' ? '切换到深色风格' : '切换到浅色风格'" @click="toggleTheme">
                <el-icon aria-hidden="true"><Moon v-if="theme === 'light'" /><Sunny v-else /></el-icon>
                {{ theme === 'light' ? '深色' : '浅色' }}
              </button>
              <DesktopWindowControls @ready="isDesktop = true" />
            </div>
          </div>
        </el-header>
        <el-main>
          <router-view />
        </el-main>
      </el-container>
    </el-container>
    <el-drawer v-model="mobileNav" title="页面导航" direction="ltr" size="min(82vw, 280px)">
      <el-menu :router="true" :default-active="activeMenu" @select="mobileNav = false">
        <el-menu-item index="/">今日待发布</el-menu-item>
        <el-menu-item index="/content">内容库</el-menu-item>
        <el-menu-item index="/publish-center">任务中心</el-menu-item>
        <el-menu-item index="/account-management">账号管理</el-menu-item>
        <el-menu-item index="/settings">设置</el-menu-item>
      </el-menu>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import DesktopWindowControls from './components/DesktopWindowControls.vue'
import {
  HomeFilled, User,
  Fold, Upload, Clock, Moon, Sunny, VideoCamera
} from '@element-plus/icons-vue'

const route = useRoute()
const isDesktop = ref(false)
const toggleWindowSize = () => window.dispatchEvent(new Event('desktop-toggle-maximize'))

// 当前激活的菜单项
const activeMenu = computed(() => route.path)
const pageTitle = computed(() => ({ '/': '今日待发布', '/content': '内容库', '/publish-center': '任务中心', '/account-management': '账号管理', '/settings': '设置' })[route.path] || '视频发布工作台')

// 侧边栏折叠状态
const isCollapse = ref(false)
const mobileNav = ref(false)
const theme = ref(localStorage.getItem('sau-theme') === 'dark' ? 'dark' : 'light')
watch(theme, value => {
  document.documentElement.dataset.theme = value
  localStorage.setItem('sau-theme', value)
}, { immediate: true })
const toggleTheme = () => { theme.value = theme.value === 'light' ? 'dark' : 'light' }

// 切换侧边栏折叠状态
const toggleSidebar = () => {
  isCollapse.value = !isCollapse.value
}
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

#app {
  min-height: 100vh;
}

.el-container {
  height: 100dvh;
  min-width: 0;
}

.el-aside {
  background: var(--ui-surface);
  color: var(--ui-text);
  height: 100dvh;
  border-right: 1px solid var(--ui-border);
  overflow: hidden;
  transition: width 0.3s;
  
  .sidebar {
    display: flex;
    flex-direction: column;
    height: 100%;
    
    .logo {
      height: 60px;
      padding: 0 16px;
      display: flex;
      align-items: center;
      background: var(--ui-surface);
      overflow: hidden;
      
    .logo-icon {
      width: 30px;
      height: 30px;
      flex: none;
      margin-right: 8px;
      }
      
      h2 {
        color: var(--ui-text);
        font-size: 16px;
        font-weight: 600;
        white-space: nowrap;
        margin: 0;
      }
    }
    
    .sidebar-menu {
      padding: 12px 10px;
      border-right: none;
      flex: 1;
      background: var(--ui-surface);
      --el-menu-bg-color: var(--ui-surface);
      --el-menu-text-color: var(--ui-text);
      --el-menu-active-color: var(--el-color-primary);
      --el-menu-hover-bg-color: var(--el-fill-color-light);
      .el-menu-item.is-active { box-shadow: var(--ui-inset); border-radius: 12px; }
      
      .el-menu-item {
        margin-bottom: 6px;
        height: 48px;
        border-radius: 10px;
        display: flex;
        align-items: center;
        
        .el-icon {
          margin-right: 10px;
          font-size: 18px;
        }
      }
    }
  }
}

.el-header {
  background: var(--ui-surface);
  border-bottom: 1px solid var(--ui-border);
  padding: 0;
  height: 60px;
  
  .header-content {
    display: flex;
    justify-content: space-between;
    align-items: center;
    height: 100%;
    padding: 0 16px;
    
    .header-left {
      display: flex;
      flex: 1;
      min-width: 0;
      height: 100%;
      align-items: center;
      gap: 12px;
      .current-page { font-size: 13px; color: var(--ui-muted); flex: 1; align-self: stretch; display: flex; align-items: center; user-select: none; }
      .toggle-sidebar {
        display: grid;
        place-items: center;
        width: 40px;
        height: 40px;
        border-radius: 9px;
        font-size: 20px;
        cursor: pointer;
        color: var(--ui-muted);
        
        &:hover {
          color: var(--el-color-primary);
          background: var(--ui-soft);
        }
      }
    }
    
    .header-right {
      display: flex;
      align-items: center;
      height: 100%;
      .theme-toggle {
        min-height: 42px;
        padding: 0 15px;
        display: inline-flex;
        align-items: center;
        gap: 8px;
        border: 1px solid var(--ui-border);
        border-radius: 13px;
        background: var(--ui-surface);
        box-shadow: var(--ui-raised-sm);
        color: var(--ui-text);
        cursor: pointer;
      }
      .user-dropdown {
        display: flex;
        align-items: center;
        cursor: pointer;
        
        .username {
          margin: 0 8px;
          color: $text-regular;
        }
        
        .el-icon {
          font-size: 12px;
          color: $text-secondary;
        }
      }
    }
  }
}

.el-main {
  background: var(--ui-bg);
  padding: 28px;
  min-width: 0;
  overflow-y: auto;
}
.mobile-menu { display: none; }
.desktop-app .el-header .header-content { padding-right: 0; }
.desktop-app .el-header .header-content .theme-toggle { border: 0; background: transparent; box-shadow: none; padding: 0 12px; min-height: 36px; border-radius: 8px; }
.desktop-app .el-header .header-content .theme-toggle:hover { background: var(--el-fill-color-light); }
.desktop-app [data-window-drag] { cursor: default; user-select: none; touch-action: none; }
.el-main { scrollbar-width: thin; scrollbar-color: var(--ui-border) transparent; }
.el-aside.is-collapsed .sidebar .logo { padding: 0; justify-content: center; }
.el-aside.is-collapsed .sidebar .logo .logo-icon { margin: 0; }
.el-aside .sidebar .sidebar-menu.el-menu--collapse { padding: 12px 8px; }
.el-aside .sidebar .sidebar-menu.el-menu--collapse .el-menu-item { padding: 0; justify-content: center; }
.el-aside .sidebar .sidebar-menu.el-menu--collapse .el-menu-item .el-icon { margin: 0; }
.sidebar-menu.el-menu--collapse :deep(.el-menu-tooltip__trigger) { padding: 0; justify-content: center; }
@media (max-width: 700px) {
  .el-aside { display: none; }
  .el-main { padding: 12px; }
  .el-header .header-content .header-left .toggle-sidebar { display: none; }
  .mobile-menu { display: inline-flex; }
}
</style>
