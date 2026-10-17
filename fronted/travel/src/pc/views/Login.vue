<template>
  <div class="login-layout">
    <div class="login-card">
      <div class="login-brand">
        <div class="brand-mark">旅</div>
        <div>
          <h1>AI 旅游助手</h1>
          <p>算法驱动 · 桌面端</p>
        </div>
      </div>

      <el-tabs v-model="activeTab" class="login-tabs">
        <el-tab-pane label="账号登录" name="login" />
        <el-tab-pane label="注册账号" name="register" />
      </el-tabs>

      <el-form label-position="top" @submit.prevent="onSubmit">
        <el-form-item label="账号">
          <el-input
            v-model="form.username"
            placeholder="3–20 位，字母/数字/下划线/中文"
            maxlength="20"
            clearable
            :prefix-icon="User"
            autocomplete="username"
          />
        </el-form-item>

        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="至少 6 位"
            maxlength="64"
            show-password
            clearable
            :prefix-icon="Lock"
            autocomplete="current-password"
            @keyup.enter="onSubmit"
          />
        </el-form-item>

        <el-form-item v-if="activeTab === 'register'" label="昵称（可不填）">
          <el-input
            v-model="form.nickname"
            placeholder="默认使用账号名"
            maxlength="20"
            clearable
          />
        </el-form-item>

        <el-button
          type="primary"
          class="login-submit"
          :loading="submitting"
          @click="onSubmit"
        >
          {{ activeTab === 'register' ? '注册并登录' : '登录' }}
        </el-button>
      </el-form>

      <div class="login-switch">
        <span v-if="activeTab === 'login'" @click="activeTab = 'register'">
          还没有账号？去注册
        </span>
        <span v-else @click="activeTab = 'login'">已有账号？去登录</span>
      </div>

      <div class="agreement">
        注册即表示同意用户协议与隐私政策。密码经哈希存储，不会明文保存。
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * 桌面端登录 / 注册：**账号 + 密码**。
 *
 * 与移动端共用 useAuth 和同一套 /api/auth/* 接口。
 * 早先的"手机号 + 短信验证码"已下线：项目没有真实短信通道，
 * 验证码是回显给前端自动填的，那道验证等于不存在。
 */
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Lock, User } from '@element-plus/icons-vue'

import { useAuth } from '@/composables/useAuth'

const route = useRoute()
const router = useRouter()
const { login, register } = useAuth()

// 支持 /pc/#/login?mode=register 直接进注册页
const activeTab = ref(route.query.mode === 'register' ? 'register' : 'login')
const submitting = ref(false)

const form = reactive({
  username: '',
  password: '',
  nickname: '',
})

async function onSubmit() {
  const username = form.username.trim()
  const password = form.password

  // 本地先粗校验；真正的规则以后端为准
  if (!/^[a-zA-Z0-9_\u4e00-\u9fa5]{3,20}$/.test(username)) {
    ElMessage.warning('账号需 3–20 位，只能是字母、数字、下划线或中文')
    return
  }
  if (password.length < 6) {
    ElMessage.warning('密码至少 6 位')
    return
  }
  if (activeTab.value === 'register' && form.nickname.trim().length > 20) {
    ElMessage.warning('昵称最长 20 个字符')
    return
  }

  submitting.value = true
  try {
    if (activeTab.value === 'register') {
      await register({
        username,
        password,
        nickname: form.nickname.trim() || undefined,
      })
      ElMessage.success('注册成功')
    } else {
      await login({ username, password })
      ElMessage.success('登录成功')
    }
    router.replace('/')
  } catch (e) {
    // 401 账号或密码错误 / 409 账号被占用 / 400 格式不符
    ElMessage.error(e?.message || '操作失败，请重试')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.login-layout {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--app-bg);
  padding: var(--app-space-lg);
}

.login-card {
  width: 100%;
  max-width: 400px;
  background: var(--app-surface);
  border: 1px solid var(--app-border);
  border-radius: var(--app-radius-lg);
  padding: 32px 32px 24px;
  box-shadow: var(--app-shadow-md);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.brand-mark {
  width: 44px;
  height: 44px;
  border-radius: var(--app-radius-md);
  background: var(--app-brand);
  color: var(--app-brand-fg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--app-font-serif);
  font-size: 22px;
  flex-shrink: 0;
}

.login-brand h1 {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  color: var(--app-text);
}

.login-brand p {
  margin: 2px 0 0;
  font-size: 11px;
  color: var(--app-text-tertiary);
  letter-spacing: 0.04em;
}

.login-tabs {
  margin-bottom: 4px;
}

.login-submit {
  width: 100%;
  margin-top: 4px;
}

.login-switch {
  margin-top: 14px;
  text-align: center;
  font-size: 13px;
  color: var(--app-brand);
  cursor: pointer;
}

.agreement {
  margin-top: 18px;
  font-size: 11px;
  line-height: 1.7;
  color: var(--app-text-tertiary);
  text-align: center;
}
</style>
