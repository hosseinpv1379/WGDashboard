<script setup>
import {onMounted, reactive, ref} from 'vue'
import {fetchGet, fetchPost} from '@/utilities/fetch.js'
import {DashboardConfigurationStore} from '@/stores/DashboardConfigurationStore.js'
import LocaleText from '@/components/text/localeText.vue'

const dashboardStore = DashboardConfigurationStore()
const nodes = ref([])
const subscriptions = ref([])
const clients = ref([])
const createdNode = ref(null)
const createdSubscription = ref(null)
const selectedNodes = reactive({})
const edits = reactive({})
const loading = ref(false)

const nodeForm = reactive({name: '', region: '', public_endpoint: '', capacity: 0})
const subscriptionForm = reactive({
  client_id: '', name: '', quota_gb: 0, expires_at: '', max_peers: 1,
})

const localDateTime = (value) => value ? String(value).replace(' ', 'T').slice(0, 16) : ''

const load = async () => {
  loading.value = true
  await fetchGet('/api/commercial/nodes', {}, (res) => { nodes.value = res.data || [] })
  await fetchGet('/api/commercial/subscriptions', {}, (res) => {
    subscriptions.value = res.data || []
    subscriptions.value.forEach((subscription) => {
      edits[subscription.SubscriptionID] = {
        name: subscription.Name,
        quota_gb: subscription.QuotaGB,
        expires_at: localDateTime(subscription.ExpiresAt),
        max_peers: subscription.MaxPeers,
        status: subscription.Status === 'disabled' ? 'disabled' : 'active',
      }
      if (!selectedNodes[subscription.SubscriptionID]) selectedNodes[subscription.SubscriptionID] = []
    })
  })
  await fetchGet('/api/clients/allClientsRaw', {}, (res) => { clients.value = res.data || [] })
  loading.value = false
}

const createNode = async () => {
  await fetchPost('/api/commercial/nodes', nodeForm, (res) => {
    createdNode.value = res.data
    Object.assign(nodeForm, {name: '', region: '', public_endpoint: '', capacity: 0})
    dashboardStore.newMessage('Server', 'Node created successfully', 'success')
  })
  await load()
}

const createSubscription = async () => {
  await fetchPost('/api/commercial/subscriptions', subscriptionForm, (res) => {
    createdSubscription.value = res.data
    Object.assign(subscriptionForm, {client_id: '', name: '', quota_gb: 0, expires_at: '', max_peers: 1})
    dashboardStore.newMessage('Server', 'Subscription created successfully', 'success')
  })
  await load()
}

const updateSubscription = async (subscriptionID) => {
  await fetchPost(`/api/commercial/subscriptions/${subscriptionID}`, edits[subscriptionID], (res) => {
    if (res.status) dashboardStore.newMessage('Server', 'Subscription updated successfully', 'success')
  })
  await load()
}

const resetUsage = async (subscriptionID) => {
  await fetchPost(`/api/commercial/subscriptions/${subscriptionID}`, {
    ...edits[subscriptionID], reset_usage: true, status: 'active',
  }, (res) => {
    if (res.status) dashboardStore.newMessage('Server', 'Subscription usage reset', 'warning')
  })
  await load()
}

const provision = async (subscriptionID) => {
  await fetchPost(`/api/commercial/subscriptions/${subscriptionID}/provision`, {
    node_ids: selectedNodes[subscriptionID] || [], interface: 'wg0', dns: '1.1.1.1',
    mtu: 1420, allowed_ips: '0.0.0.0/0, ::/0',
  }, (res) => {
    if (res.status) dashboardStore.newMessage('Server', 'Provision jobs queued', 'success')
  })
  selectedNodes[subscriptionID] = []
  await load()
}

const rotateToken = async (subscriptionID) => {
  await fetchPost(`/api/commercial/subscriptions/${subscriptionID}/rotate-token`, {}, (res) => {
    createdSubscription.value = res.data
    dashboardStore.newMessage('Server', 'Subscription link rotated', 'warning')
  })
}

const peerAction = async (peerID, operation) => {
  await fetchPost(`/api/commercial/peers/${peerID}/action`, {operation}, (res) => {
    if (res.status) dashboardStore.newMessage('Server', 'Node job queued', 'success')
  })
  await load()
}

const copy = async (value) => {
  await navigator.clipboard.writeText(value)
  dashboardStore.newMessage('WGDashboard', 'Copied', 'success')
}

onMounted(load)
</script>

<template>
  <div class="container-fluid pb-4">
    <div class="d-flex align-items-center mb-4">
      <div>
        <h2 class="mb-1"><LocaleText t="Commercial subscriptions"></LocaleText></h2>
        <p class="text-muted mb-0"><LocaleText t="Manage timed, metered and multi-server WireGuard access"></LocaleText></p>
      </div>
      <button class="btn btn-sm btn-outline-body ms-auto rounded-3" :disabled="loading" @click="load">
        <i class="bi bi-arrow-clockwise me-2"></i><LocaleText t="Refresh"></LocaleText>
      </button>
    </div>

    <div v-if="createdNode" class="alert alert-warning rounded-3">
      <strong><LocaleText t="Save this node token now"></LocaleText></strong>
      <div class="input-group mt-2">
        <input class="form-control font-monospace" readonly :value="createdNode.agent_token">
        <button class="btn btn-outline-dark" @click="copy(createdNode.agent_token)"><LocaleText t="Copy"></LocaleText></button>
      </div>
      <small>Node ID: <code>{{ createdNode.node_id }}</code></small>
    </div>

    <div v-if="createdSubscription" class="alert alert-info rounded-3">
      <strong><LocaleText t="Save this subscription link now"></LocaleText></strong>
      <div class="input-group mt-2">
        <input class="form-control font-monospace" readonly :value="createdSubscription.subscription_url">
        <button class="btn btn-outline-dark" @click="copy(createdSubscription.subscription_url)"><LocaleText t="Copy"></LocaleText></button>
      </div>
      <small><LocaleText t="Add ?format=zip to download all configurations as a ZIP file"></LocaleText></small>
    </div>

    <div class="row g-3 mb-4">
      <div class="col-xl-5">
        <div class="card rounded-3 h-100">
          <div class="card-header bg-transparent"><strong><LocaleText t="Add WireGuard node"></LocaleText></strong></div>
          <form class="card-body row g-2" @submit.prevent="createNode">
            <div class="col-md-6"><label class="form-label"><LocaleText t="Name"></LocaleText></label><input v-model.trim="nodeForm.name" required class="form-control"></div>
            <div class="col-md-6"><label class="form-label"><LocaleText t="Region"></LocaleText></label><input v-model.trim="nodeForm.region" class="form-control" placeholder="Germany"></div>
            <div class="col-12"><label class="form-label"><LocaleText t="Public endpoint"></LocaleText></label><input v-model.trim="nodeForm.public_endpoint" class="form-control" placeholder="vpn.example.com:51820"></div>
            <div class="col-md-6"><label class="form-label"><LocaleText t="Capacity"></LocaleText></label><input v-model.number="nodeForm.capacity" min="0" type="number" class="form-control"></div>
            <div class="col-12 d-flex"><button class="btn btn-dark btn-brand ms-auto" type="submit"><i class="bi bi-hdd-rack me-2"></i><LocaleText t="Create node"></LocaleText></button></div>
          </form>
        </div>
      </div>
      <div class="col-xl-7">
        <div class="card rounded-3 h-100">
          <div class="card-header bg-transparent"><strong><LocaleText t="Create subscription"></LocaleText></strong></div>
          <form class="card-body row g-2" @submit.prevent="createSubscription">
            <div class="col-md-6"><label class="form-label"><LocaleText t="Client"></LocaleText></label><select v-model="subscriptionForm.client_id" required class="form-select"><option value="" disabled>Select client</option><option v-for="client in clients" :key="client.ClientID" :value="client.ClientID">{{ client.Name || client.Email }} — {{ client.Email }}</option></select></div>
            <div class="col-md-6"><label class="form-label"><LocaleText t="Subscription name"></LocaleText></label><input v-model.trim="subscriptionForm.name" required class="form-control"></div>
            <div class="col-md-4"><label class="form-label"><LocaleText t="Data Quota"></LocaleText> (GB)</label><input v-model.number="subscriptionForm.quota_gb" min="0" step="0.1" type="number" class="form-control"></div>
            <div class="col-md-5"><label class="form-label"><LocaleText t="Expiration time"></LocaleText></label><input v-model="subscriptionForm.expires_at" type="datetime-local" class="form-control"></div>
            <div class="col-md-3"><label class="form-label"><LocaleText t="Maximum servers"></LocaleText></label><input v-model.number="subscriptionForm.max_peers" min="1" type="number" class="form-control"></div>
            <div class="col-12 d-flex"><button class="btn btn-dark btn-brand ms-auto" type="submit"><i class="bi bi-plus-circle me-2"></i><LocaleText t="Create subscription"></LocaleText></button></div>
          </form>
        </div>
      </div>
    </div>

    <div class="card rounded-3 mb-4">
      <div class="card-header bg-transparent"><strong><LocaleText t="Nodes"></LocaleText></strong></div>
      <div class="table-responsive">
        <table class="table align-middle mb-0">
          <thead><tr><th><LocaleText t="Name"></LocaleText></th><th><LocaleText t="Region"></LocaleText></th><th><LocaleText t="Status"></LocaleText></th><th><LocaleText t="Endpoint"></LocaleText></th><th><LocaleText t="Last seen"></LocaleText></th><th>Version</th></tr></thead>
          <tbody><tr v-for="node in nodes" :key="node.NodeID"><td>{{ node.Name }}<br><code class="small">{{ node.NodeID }}</code></td><td>{{ node.Region || '—' }}</td><td><span class="badge" :class="node.Status === 'online' ? 'bg-success-subtle text-success-emphasis' : 'bg-secondary-subtle text-secondary-emphasis'">{{ node.Status }}</span></td><td>{{ node.PublicEndpoint || '—' }}</td><td>{{ node.LastSeenAt || 'Never' }}</td><td>{{ node.AgentVersion || '—' }}</td></tr><tr v-if="!nodes.length"><td colspan="6" class="text-muted text-center py-4"><LocaleText t="No nodes registered"></LocaleText></td></tr></tbody>
        </table>
      </div>
    </div>

    <div class="d-flex flex-column gap-3">
      <div class="card rounded-3" v-for="subscription in subscriptions" :key="subscription.SubscriptionID">
        <div class="card-header bg-transparent d-flex align-items-center gap-2 flex-wrap">
          <strong>{{ subscription.Name }}</strong>
          <span class="badge bg-secondary-subtle text-secondary-emphasis">{{ subscription.Status }}</span>
          <span class="ms-auto small text-muted">{{ subscription.UsedGB }} / {{ subscription.QuotaGB || '∞' }} GB · {{ subscription.ExpiresAt || 'Unlimited time' }}</span>
        </div>
        <div class="card-body">
          <div class="row g-2 align-items-end mb-3" v-if="edits[subscription.SubscriptionID]">
            <div class="col-md-3"><label class="form-label small"><LocaleText t="Name"></LocaleText></label><input v-model="edits[subscription.SubscriptionID].name" class="form-control form-control-sm"></div>
            <div class="col-md-2"><label class="form-label small"><LocaleText t="Quota"></LocaleText> GB</label><input v-model.number="edits[subscription.SubscriptionID].quota_gb" min="0" step="0.1" type="number" class="form-control form-control-sm"></div>
            <div class="col-md-3"><label class="form-label small"><LocaleText t="Expiration time"></LocaleText></label><input v-model="edits[subscription.SubscriptionID].expires_at" type="datetime-local" class="form-control form-control-sm"></div>
            <div class="col-md-1"><label class="form-label small">Max</label><input v-model.number="edits[subscription.SubscriptionID].max_peers" min="1" type="number" class="form-control form-control-sm"></div>
            <div class="col-md-2"><label class="form-label small"><LocaleText t="Status"></LocaleText></label><select v-model="edits[subscription.SubscriptionID].status" class="form-select form-select-sm"><option value="active">active</option><option value="disabled">disabled</option></select></div>
            <div class="col-md-1"><button class="btn btn-sm btn-primary w-100" @click="updateSubscription(subscription.SubscriptionID)"><i class="bi bi-save"></i></button></div>
          </div>

          <div class="d-flex gap-2 flex-wrap mb-3">
			<label class="form-check" :class="{'opacity-50': node.Status !== 'online'}" v-for="node in nodes" :key="node.NodeID"><input class="form-check-input" type="checkbox" :disabled="node.Status !== 'online'" :value="node.NodeID" v-model="selectedNodes[subscription.SubscriptionID]"><span class="form-check-label">{{ node.Name }}</span></label>
			<button class="btn btn-sm btn-dark ms-md-auto" :disabled="!(selectedNodes[subscription.SubscriptionID] || []).length" @click="provision(subscription.SubscriptionID)"><i class="bi bi-cloud-upload me-2"></i><LocaleText t="Provision selected nodes"></LocaleText></button>
			<button class="btn btn-sm btn-outline-info" @click="resetUsage(subscription.SubscriptionID)"><i class="bi bi-arrow-counterclockwise me-2"></i><LocaleText t="Reset usage"></LocaleText></button>
			<button class="btn btn-sm btn-outline-warning" @click="rotateToken(subscription.SubscriptionID)"><i class="bi bi-arrow-repeat me-2"></i><LocaleText t="Rotate link"></LocaleText></button>
          </div>

          <div class="table-responsive">
            <table class="table table-sm align-middle mb-0"><thead><tr><th><LocaleText t="Node"></LocaleText></th><th><LocaleText t="Address"></LocaleText></th><th><LocaleText t="Status"></LocaleText></th><th><LocaleText t="Usage"></LocaleText></th><th></th></tr></thead><tbody><tr v-for="peer in subscription.Peers" :key="peer.SubscriptionPeerID"><td>{{ peer.NodeName }} <span class="text-muted">{{ peer.NodeRegion }}</span></td><td><code>{{ peer.Address || 'pending' }}</code></td><td>{{ peer.Status }}</td><td>{{ (peer.UsedBytes / 1073741824).toFixed(4) }} GB</td><td class="text-end"><button v-if="peer.Status === 'active'" class="btn btn-sm btn-outline-warning" @click="peerAction(peer.SubscriptionPeerID, 'DISABLE_PEER')">Disable</button><button v-if="peer.Status === 'disabled'" class="btn btn-sm btn-outline-success" @click="peerAction(peer.SubscriptionPeerID, 'ENABLE_PEER')">Enable</button><button class="btn btn-sm btn-outline-danger ms-1" @click="peerAction(peer.SubscriptionPeerID, 'DELETE_PEER')">Delete</button></td></tr><tr v-if="!subscription.Peers.length"><td colspan="5" class="text-muted text-center"><LocaleText t="No servers provisioned"></LocaleText></td></tr></tbody></table>
          </div>
        </div>
      </div>
      <div v-if="!subscriptions.length" class="card"><div class="card-body text-center text-muted py-5"><LocaleText t="No subscriptions created"></LocaleText></div></div>
    </div>
  </div>
</template>
