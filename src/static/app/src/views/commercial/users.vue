<script setup>
import {computed, onMounted, reactive, ref} from 'vue'
import {fetchGet, fetchPost} from '@/utilities/fetch.js'
import {DashboardConfigurationStore} from '@/stores/DashboardConfigurationStore.js'

const dashboardStore = DashboardConfigurationStore()
const users = ref([])
const search = ref('')
const loading = ref(false)
const resetLink = ref('')
const form = reactive({Email: '', Name: '', Password: '', ConfirmPassword: ''})

const filteredUsers = computed(() => {
  const query = search.value.trim().toLowerCase()
  if (!query) return users.value
  return users.value.filter((user) => `${user.Name || ''} ${user.Email || ''}`.toLowerCase().includes(query))
})

const load = async () => {
  loading.value = true
  await fetchGet('/api/commercial/users', {}, (response) => { users.value = response.data || [] })
  loading.value = false
}

const createUser = async () => {
  await fetchPost('/api/clients/createClient', form, (response) => {
    if (!response.status) return
    Object.assign(form, {Email: '', Name: '', Password: '', ConfirmPassword: ''})
    dashboardStore.newMessage('Users', 'User created', 'success')
  })
  await load()
}

const saveName = async (user) => {
  await fetchPost('/api/clients/updateProfileName', {ClientID: user.ClientID, Name: user.Name}, (response) => {
    if (response.status) dashboardStore.newMessage('Users', 'User updated', 'success')
  })
  await load()
}

const createResetLink = async (user) => {
  await fetchPost('/api/clients/generatePasswordResetLink', {ClientID: user.ClientID}, (response) => {
    if (!response.status) return
    const url = new URL('client/', window.location.href)
    url.hash = `/forgotPassword?email=${encodeURIComponent(user.Email)}&token=${encodeURIComponent(response.data)}`
    resetLink.value = url.href
    dashboardStore.newMessage('Users', 'Password reset link generated', 'success')
  })
}

const deleteUser = async (user) => {
  if (!window.confirm(`Delete ${user.Email}?`)) return
  await fetchPost(`/api/commercial/users/${user.ClientID}/delete`, {}, (response) => {
    if (response.status) dashboardStore.newMessage('Users', 'User deleted', 'warning')
  })
  await load()
}

const copy = async (value) => {
  await navigator.clipboard.writeText(value)
  dashboardStore.newMessage('Users', 'Copied', 'success')
}

onMounted(load)
</script>

<template>
  <div>
    <div v-if="resetLink" class="alert alert-warning rounded-3">
      <strong>This password reset link expires in 30 minutes.</strong>
      <div class="input-group mt-2"><input class="form-control font-monospace" readonly :value="resetLink"><button class="btn btn-outline-dark" @click="copy(resetLink)">Copy</button></div>
    </div>

    <div class="card rounded-3 mb-4">
      <div class="card-header bg-transparent"><strong>Create user</strong></div>
      <form class="card-body row g-3" @submit.prevent="createUser">
        <div class="col-md-3"><label class="form-label">Name</label><input v-model.trim="form.Name" class="form-control" placeholder="Customer name"></div>
        <div class="col-md-3"><label class="form-label">Email</label><input v-model.trim="form.Email" required type="email" class="form-control" placeholder="customer@example.com"></div>
        <div class="col-md-3"><label class="form-label">Password</label><input v-model="form.Password" required minlength="8" type="password" class="form-control" autocomplete="new-password"></div>
        <div class="col-md-3"><label class="form-label">Confirm password</label><input v-model="form.ConfirmPassword" required minlength="8" type="password" class="form-control" autocomplete="new-password"></div>
        <div class="col-12 text-end"><button class="btn btn-dark" :disabled="form.Password !== form.ConfirmPassword" type="submit"><i class="bi bi-person-plus me-2"></i>Create user</button></div>
      </form>
    </div>

    <div class="card rounded-3">
      <div class="card-header bg-transparent d-flex align-items-center gap-2"><strong>Users</strong><input v-model="search" class="form-control form-control-sm ms-auto" style="max-width: 280px" placeholder="Search name or email"><button class="btn btn-sm btn-outline-secondary" :disabled="loading" @click="load"><i class="bi bi-arrow-clockwise"></i></button></div>
      <div class="table-responsive">
        <table class="table align-middle mb-0">
          <thead><tr><th>Name</th><th>Email</th><th>Type</th><th>Subscriptions</th><th></th></tr></thead>
          <tbody>
            <tr v-for="user in filteredUsers" :key="user.ClientID">
              <td><input v-model.trim="user.Name" class="form-control form-control-sm" placeholder="No name"></td>
              <td>{{ user.Email }}</td><td>{{ user.ClientGroup === 'Local' ? 'Local' : 'SSO' }}</td><td>{{ user.SubscriptionCount }}</td>
              <td class="text-end text-nowrap"><button class="btn btn-sm btn-outline-primary" @click="saveName(user)">Save</button><button v-if="user.ClientGroup === 'Local'" class="btn btn-sm btn-outline-warning ms-1" @click="createResetLink(user)">Reset link</button><button class="btn btn-sm btn-outline-danger ms-1" :disabled="user.SubscriptionCount > 0" title="Users with subscription history cannot be deleted" @click="deleteUser(user)">Delete</button></td>
            </tr>
            <tr v-if="!filteredUsers.length"><td colspan="5" class="text-center text-muted py-4">No users found</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
