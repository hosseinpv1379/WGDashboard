<script setup>
import {onMounted, reactive, ref} from 'vue'
import {fetchGet, fetchPost} from '@/utilities/fetch.js'
import {DashboardConfigurationStore} from '@/stores/DashboardConfigurationStore.js'

const dashboardStore = DashboardConfigurationStore()
const nodes = ref([])
const createdNode = ref(null)
const loading = ref(false)
const form = reactive({name: '', region: '', public_endpoint: '', capacity: 0})

const load = async () => {
  loading.value = true
  await fetchGet('/api/commercial/nodes', {}, (response) => { nodes.value = response.data || [] })
  loading.value = false
}

const createNode = async () => {
  await fetchPost('/api/commercial/nodes', form, (response) => {
    createdNode.value = response.data
    Object.assign(form, {name: '', region: '', public_endpoint: '', capacity: 0})
    dashboardStore.newMessage('Nodes', 'Node created successfully', 'success')
  })
  await load()
}

const revokeNode = async (node) => {
  if (!window.confirm(`Remove ${node.Name}? It will be removed from node groups and subscriptions. The agent will clean up its peers before access is revoked.`)) return
  await fetchPost(`/api/commercial/nodes/${node.NodeID}/revoke`, {}, (response) => {
    if (response.status) dashboardStore.newMessage('Nodes', 'Node removal started; subscriptions were updated immediately', 'warning')
  })
  await load()
}

const copy = async (value) => {
  await navigator.clipboard.writeText(value)
  dashboardStore.newMessage('Nodes', 'Copied', 'success')
}

onMounted(load)
</script>

<template>
  <div>
    <div v-if="createdNode" class="alert alert-warning rounded-3">
      <strong>Save this token now. It is shown only once.</strong>
      <div class="input-group mt-2">
        <input class="form-control font-monospace" readonly :value="createdNode.agent_token">
        <button class="btn btn-outline-dark" @click="copy(createdNode.agent_token)">Copy</button>
      </div>
      <small>Node ID: <code>{{ createdNode.node_id }}</code></small>
    </div>

    <div class="card rounded-3 mb-4">
      <div class="card-header bg-transparent"><strong>Add WireGuard node</strong></div>
      <form class="card-body row g-3" @submit.prevent="createNode">
        <div class="col-md-3"><label class="form-label">Name</label><input v-model.trim="form.name" required class="form-control" placeholder="germany-1"></div>
        <div class="col-md-2"><label class="form-label">Location</label><input v-model.trim="form.region" required class="form-control" placeholder="Germany"></div>
        <div class="col-md-4"><label class="form-label">Public endpoint</label><input v-model.trim="form.public_endpoint" required class="form-control" placeholder="de1.example.com:51820"></div>
        <div class="col-md-2"><label class="form-label">Capacity</label><input v-model.number="form.capacity" min="0" type="number" class="form-control"><div class="form-text">0 = unlimited</div></div>
        <div class="col-md-1 d-flex align-items-start pt-md-4"><button class="btn btn-dark w-100" type="submit"><i class="bi bi-plus-lg"></i></button></div>
      </form>
    </div>

    <div class="card rounded-3">
      <div class="card-header bg-transparent d-flex align-items-center"><strong>Nodes</strong><button class="btn btn-sm btn-outline-secondary ms-auto" :disabled="loading" @click="load"><i class="bi bi-arrow-clockwise"></i></button></div>
      <div class="table-responsive">
        <table class="table align-middle mb-0">
          <thead><tr><th>Name</th><th>Location</th><th>Status</th><th>Interfaces</th><th>Capacity</th><th>Last seen</th><th></th></tr></thead>
          <tbody>
            <tr v-for="node in nodes" :key="node.NodeID">
              <td><strong>{{ node.Name }}</strong><br><code class="small">{{ node.NodeID }}</code></td>
              <td>{{ node.Region || '—' }}</td>
              <td><span class="badge" :class="node.Status === 'online' ? 'text-bg-success' : node.Status === 'revoking' ? 'text-bg-warning' : 'text-bg-secondary'">{{ node.Status }}</span></td>
              <td>
                <div v-for="item in node.Interfaces" :key="item.InterfaceName" class="mb-1">
                  <code>{{ item.InterfaceName }}</code>
                  <span class="text-muted small"> · {{ item.AddressPool || 'pool unknown' }} · {{ item.PublicEndpoint || node.PublicEndpoint || 'endpoint unknown' }}</span>
                </div>
                <span v-if="!node.Interfaces?.length" class="text-muted">Waiting for agent heartbeat</span>
              </td>
              <td>{{ node.Capacity || 'Unlimited' }}</td>
              <td>{{ node.LastSeenAt || 'Never' }}</td>
              <td class="text-end"><button class="btn btn-sm btn-outline-danger" @click="revokeNode(node)">{{ node.Status === 'revoking' ? 'Retry removal' : 'Remove' }}</button></td>
            </tr>
            <tr v-if="!nodes.length"><td colspan="7" class="text-center text-muted py-4">No nodes registered</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
