<template>
  <div class="about">
    <el-card class="about-card">
      <div class="about-header">
        <h1>视频发布工作台</h1>
        <p class="version">social-auto-upload</p>
      </div>

      <el-divider />

      <div class="about-section">
        <h3>系统简介</h3>
        <p>
          在本机管理视频、文案和发布账号，统一查看多平台投稿任务。
          支持 AI 日报资源包与普通视频的立即投稿、预约和结果核对。
        </p>
      </div>

      <div class="about-section">
        <h3>统一投稿平台</h3>
        <div class="platform-tags">
          <el-tag>B站</el-tag>
          <el-tag type="danger">抖音</el-tag>
          <el-tag type="warning">视频号</el-tag>
          <el-tag>YouTube</el-tag>
          <el-tag>今日头条</el-tag>
        </div>
        <p class="platform-note">YouTube、今日头条已接入浏览器投稿通道，实际投稿仍待真实账号验收。</p>
      </div>

      <div class="about-section">
        <h3>核心功能</h3>
        <ul class="feature-list">
          <li>多平台账号管理与登录状态维护</li>
          <li>视频素材上传与管理</li>
          <li>按平台编辑文案、受众与封面设置</li>
          <li>选择平台立即投稿或预约发布</li>
          <li>查看任务进度与核对发布结果</li>
        </ul>
      </div>

      <div class="about-section">
        <h3>技术栈</h3>
        <div class="tech-tags">
          <el-tag effect="plain">Vue 3</el-tag>
          <el-tag effect="plain">Element Plus</el-tag>
          <el-tag effect="plain">Pinia</el-tag>
          <el-tag effect="plain">Flask</el-tag>
          <el-tag effect="plain">Playwright</el-tag>
          <el-tag effect="plain">SQLite</el-tag>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
// 关于页面组件
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.about {
  max-width: 700px;
  margin: 0 auto;

  .about-card {
    .about-header {
      text-align: center;

      h1 {
        color: var(--ui-text);
        margin: 0 0 8px 0;
        font-size: 24px;
      }

      .version {
        color: var(--ui-muted);
        font-size: 14px;
        margin: 0;
      }
    }

    .about-section {
      margin-bottom: 24px;

      h3 {
        font-size: 16px;
        color: var(--ui-text);
        margin: 0 0 12px 0;
      }

      p {
        color: var(--ui-muted);
        line-height: 1.8;
        margin: 0;
      }

      .platform-tags,
      .tech-tags {
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
      }

      .feature-list {
        margin: 0;
        padding-left: 20px;
        color: var(--ui-muted);
        line-height: 2;
      }
    }
  }
}
.about .about-card .about-section .platform-note { font-size: 13px; margin-top: 14px; }
</style>
