<template>
  <div class="login-page">
    <van-nav-bar
      :title="activeTab === 1 ? '注册账号' : '登录'"
      left-arrow
      @click-left="onBack"
    />

    <div class="login-body">
      <div class="login-brand">
        <div class="brand-mark">旅</div>
        <div class="brand-text">
          <h1>AI 旅游助手</h1>
          <p>登录后行程会云端保存，换设备也能回看</p>
        </div>
      </div>

      <!-- 账号密码：不依赖短信通道。
           注意必须放**两个** van-tab：Vant 会把 active 钳制在标签数量内，
           只放一个时 activeTab=1 会被立刻弹回 0，切不到注册。 -->
      <van-tabs v-model:active="activeTab" class="login-tabs">
        <van-tab title="账号登录" />
        <van-tab title="注册账号" />

        <van-field
          v-model="form.username"
          label="账号"
          placeholder="3–20 位，字母/数字/下划线/中文"
          maxlength="20"
          clearable
          autocomplete="username"
        />
        <van-field
          v-model="form.password"
          type="password"
          label="密码"
          placeholder="至少 6 位"
          maxlength="64"
          clearable
          autocomplete="current-password"
          @keyup.enter="onSubmit"
        />
        <van-field
          v-if="activeTab === 1"
          v-model="form.nickname"
          label="昵称"
          placeholder="可不填，默认用账号名"
          maxlength="20"
          clearable
        />

        <div class="login-actions">
          <van-button
            type="primary"
            block
            round
            :loading="submitting"
            loading-text="处理中…"
            @click="onSubmit"
          >
            {{ submitText }}
          </van-button>
        </div>

        <div class="login-switch">
          <span v-if="activeTab === 0" @click="activeTab = 1">还没有账号？去注册</span>
          <span v-else @click="activeTab = 0">已有账号？去登录</span>
        </div>
      </van-tabs>

      <div class="agreement">
        注册即表示同意用户协议与隐私政策。密码经哈希存储，不会明文保存。
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * 登录 / 注册页：**账号 + 密码**。
 *
 * 早先这里是「手机号 + 短信验证码」，但项目没接真实短信通道——
 * 验证码是回显给前端自动填的，那道"验证"等于不存在。
 * 改成账号密码后：
 *   - 不再依赖一个假的短信通道
 *   - 密码用 pbkdf2 哈希存储（后端 security.hash_password）
 *   - 账号不存在与密码错误返回同一条消息，避免账号枚举
 */
import { computed, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast } from 'vant'

import { useAuth } from '@/composables/useAuth'

const route = useRoute()
const router = useRouter()
const { login, register } = useAuth()

// 支持 /login?mode=register 直接进注册页（首页的"注册"入口会带这个参数）
const activeTab = ref(route.query.mode === 'register' ? 1 : 0)
const submitting = ref(false)

const form = reactive({
  username: '',
  password: '',
  nickname: '',
})

const submitText = computed(() => (activeTab.value === 1 ? '注册并登录' : '登录'))

function onBack() {
  if (window.history.length > 1) router.back()
  else router.replace('/')
}

async function onSubmit() {
  const username = form.username.trim()
  const password = form.password

  // 本地先粗校验，避免明显不合规的输入白跑一次网络请求。
  // 真正的规则以后端为准，这里只是提前反馈。
  if (!/^[a-zA-Z0-9_\u4e00-\u9fa5]{3,20}$/.test(username)) {
    showToast('账号需 3–20 位，只能是字母、数字、下划线或中文')
    return
  }
  if (password.length < 6) {
    showToast('密码至少 6 位')
    return
  }
  if (activeTab.value === 1 && form.nickname.trim().length > 20) {
    showToast('昵称最长 20 个字符')
    return
  }

  submitting.value = true
  try {
    if (activeTab.value === 1) {
      await register({
        username,
        password,
        nickname: form.nickname.trim() || undefined,
      })
      showToast('注册成功')
    } else {
      await login({ username, password })
      showToast('登录成功')
    }
    router.replace('/')
  } catch (e) {
    // 401 账号或密码错误 / 409 账号被占用 / 400 格式不符 都会走到这里
    showToast(e?.message || '操作失败，请重试')
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  min-height: 100dvh;
  background: var(--app-bg);
}

.login-body {
  padding: var(--app-space-lg) var(--app-space-md) var(--app-space-2xl);
}

.login-brand {
  display: flex;
  align-items: center;
  gap: var(--app-space-md);
  padding: var(--app-space-md) 0 var(--app-space-lg);
}

.brand-mark {
  width: 48px;
  height: 48px;
  border-radius: var(--app-radius-lg);
  background: var(--app-brand);
  color: var(--app-brand-fg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--app-font-serif);
  font-size: 24px;
  flex-shrink: 0;
}

.brand-text h1 {
  margin: 0;
  font-size: 19px;
  font-weight: 600;
  color: var(--app-text);
}

.brand-text p {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--app-text-secondary);
  line-height: 1.5;
}

.login-tabs {
  background: var(--app-surface);
  border-radius: var(--app-radius-lg);
  overflow: hidden;
  padding-bottom: var(--app-space-md);
}

.login-actions {
  padding: var(--app-space-lg) var(--app-space-md) 0;
}

.login-switch {
  padding: var(--app-space-md) var(--app-space-md) 0;
  text-align: center;
  font-size: 13px;
  color: var(--app-brand);
  cursor: pointer;
}

.agreement {
  margin-top: var(--app-space-lg);
  font-size: 11px;
  line-height: 1.7;
  color: var(--app-text-tertiary);
  text-align: center;
  padding: 0 var(--app-space-md);
}
</style>
