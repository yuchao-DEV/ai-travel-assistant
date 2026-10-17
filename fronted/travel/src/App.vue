<script setup>
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <router-view />
  <van-tabbar v-if="!route.meta.hideTabbar" route placeholder>
    <van-tabbar-item replace to="/" icon="home-o">首页</van-tabbar-item>
    <van-tabbar-item replace to="/agent" icon="friends-o">AI 助手</van-tabbar-item>
    <van-tabbar-item replace to="/mine" icon="user-o">我的</van-tabbar-item>
  </van-tabbar>
</template>

<style scoped></style>
