<script setup>
import {computed, onMounted, reactive, ref} from 'vue'
import {fetchGet, fetchPost} from '@/utilities/fetch.js'
import {DashboardConfigurationStore} from '@/stores/DashboardConfigurationStore.js'

const dashboardStore = DashboardConfigurationStore()
const subscriptions = ref([])
const packages = ref([])
const clients = ref([])
const nodes = ref([])
const createdSubscription = ref(null)
const loading = ref(false)
const selectedNodes = reactive({})
const edits = reactive({})
const form = reactive({client_id: '', package_id: '', name: ''})

const selectedPackage = computed(() => packages.value.find((item) => item.PackageID === form.package_id))
const localDateTime = (value) => value ? String(value).replace(' ', 'T').slice(0, 16) : ''
const money = (value) => new Intl.NumberFormat().format(Number(value || 0))
const clientLabel = (clientID) => {
  const client = clients.value.find((item) => item.ClientID === clientID)
  return client ? `${client.Name || client.Email} — ${client.Email}` : clientID
}

const load = async () => {
  loading.value = true
  await fetchGet('/api/commercial/packages', {active_only: true}, (response) => { packages.value = response.data || [] })
  await fetchGet('/api/clients/allClientsRaw', {}, (response) => { clients.value = response.data || [] })
  await fetchGet('/api/commercial/nodes', {}, (response) => { nodes.value = response.data || [] })
  await fetchGet('/api/commercial/subscriptions', {}, (response) => {
    subscriptions.value = response.data || []
    subscriptions.value.forEach((subscription) => {
      edits[subscription.SubscriptionID] = {
        name: subscription.Name,
        quota_gb: subscription.QuotaGB,
        expires_at: localDateTime(subscription.ExpiresAt),
        max_peers: subscription.MaxPeers,
        status: ['active', 'disabled'].includes(subscription.Status) ? subscription.Status : 'disabled',
      }
      if (!selectedNodes[subscription.SubscriptionID]) selectedNodes[subscription.SubscriptionID] = []
    })
  })
  loading.value = false
}

const createSubscription = async () => {
  await fetchPost('/api/commercial/subscriptions', form, (response) => {
    createdSubscription.value = response.data
    Object.assign(form, {client_id: '', package_id: '', name: ''})
    dashboardStore.newMessage('Subscriptions', `Subscription created on ${response.data.provisioned_nodes} node(s)`, 'success')
  })
  await load()
}

const updateSubscription = async (subscriptionID) => {
  await fetchPost(`/api/commercial/subscriptions/${subscriptionID}`, edits[subscriptionID], (response) => {
    if (response.status) dashboardStore.newMessage('Subscriptions', 'Subscription updated', 'success')
  })
  await load()
}

const resetUsage = async (subscriptionID) => {
  await fetchPost(`/api/commercial/subscriptions/${subscriptionID}`, {
    ...edits[subscriptionID], reset_usage: true, status: 'active',
  }, (response) => {
    if (response.status) dashboardStore.newMessage('Subscriptions', 'Usage reset', 'warning')
  })
  await load()
}

const provisionLegacy = async (subscriptionID) => {
  await fetchPost(`/api/commercial/subscriptions/${subscriptionID}/provision`, {
    node_ids: selectedNodes[subscriptionID] || [], interface: 'wg0', dns: '1.1.1.1',
    mtu: 1420, allowed_ips: '0.0.0.0/0, ::/0',
  }, (response) => {
    if (response.status) dashboardStore.newMessage('Subscriptions', 'Provision jobs queued', 'success')
  })
  selectedNodes[subscriptionID] = []
  await load()
}

const rotateToken = async (subscriptionID) => {
  await fetchPost(`/api/commercial/subscriptions/${subscriptionID}/rotate-token`, {}, (response) => {
    createdSubscription.value = response.data
    dashboardStore.newMessage('Subscriptions', 'Subscription link rotated', 'warning')
  })
}

const peerAction = async (peerID, operation) => {
  await fetchPost(`/api/commercial/peers/${peerID}/action`, {operation}, (response) => {
    if (response.status) dashboardStore.newMessage('Subscriptions', 'Node job queued', 'success')
  })
  await load()
}

const copy = async (value) => {
  await navigator.clipboard.writeText(value)
  dashboardStore.newMessage('Subscriptions', 'Copied', 'success')
}

onMounted(load)
</script>

<template>
  <div>
    <div v-if="createdSubscription" class="alert alert-info rounded-3">
      <strong>Save this subscription link now.</strong>
      <div class="input-group mt-2">
        <input class="form-control font-monospace" readonly :value="createdSubscription.subscription_url">
        <button class="btn btn-outline-dark" @click="copy(createdSubscription.subscription_url)">Copy</button>
      </div>
      <small>Add <code>?format=zip</code> to download all WireGuard configurations.</small>
    </div>

    <div class="card rounded-3 mb-4">
      <div class="card-header bg-transparent"><strong>Sell / create subscription</strong></div>
      <form class="card-body row g-3" @submit.prevent="createSubscription">
        <div class="col-md-5"><label class="form-label">User</label><select v-model="form.client_id" required class="form-select"><option value="" disabled>Select user</option><option v-for="client in clients" :key="client.ClientID" :value="client.ClientID">{{ client.Name || client.Email }} — {{ client.Email }}</option></select></div>
        <div class="col-md-4"><label class="form-label">Package</label><select v-model="form.package_id" required class="form-select"><option value="" disabled>Select package</option><option v-for="item in packages" :key="item.PackageID" :value="item.PackageID">{{ item.Name }} — {{ money(item.Price) }} {{ item.Currency }}</option></select></div>
        <div class="col-md-3"><label class="form-label">Custom title <span class="text-muted">(optional)</span></label><input v-model.trim="form.name" class="form-control" placeholder="Customer plan"></div>
        <div v-if="selectedPackage" class="col-12">
          <div class="rounded-3 bg-body-secondary p-3 d-flex flex-wrap gap-4">
            <span><strong>{{ selectedPackage.QuotaGB || '∞' }}</strong> GB</span>
            <span><strong>{{ selectedPackage.DurationDays || '∞' }}</strong> days</span>
            <span><strong>{{ selectedPackage.NodeCount }}</strong> locations</span>
            <span><strong>{{ selectedPackage.NodeGroupName }}</strong></span>
            <span class="ms-md-auto"><strong>{{ money(selectedPackage.Price) }} {{ selectedPackage.Currency }}</strong></span>
          </div>
        </div>
        <div class="col-12 text-end"><button class="btn btn-dark" :disabled="!form.client_id || !form.package_id" type="submit"><i class="bi bi-bag-check me-2"></i>Create and provision</button></div>
      </form>
    </div>

    <div class="d-flex align-items-center mb-3"><h5 class="mb-0">Subscriptions</h5><button class="btn btn-sm btn-outline-secondary ms-auto" :disabled="loading" @click="load"><i class="bi bi-arrow-clockwise"></i></button></div>
    <div class="d-flex flex-column gap-3">
      <div v-for="subscription in subscriptions" :key="subscription.SubscriptionID" class="card rounded-3">
        <div class="card-header bg-transparent d-flex align-items-center gap-2 flex-wrap">
          <strong>{{ subscription.Name }}</strong>
          <span class="badge text-bg-secondary">{{ subscription.Status }}</span>
          <span v-if="subscription.Plan" class="badge text-bg-info">{{ subscription.Plan.PackageName }}</span>
          <span class="small text-muted">{{ clientLabel(subscription.ClientID) }}</span>
          <span class="ms-auto small text-muted">{{ subscription.UsedGB }} / {{ subscription.QuotaGB || '∞' }} GB · {{ subscription.ExpiresAt || 'Unlimited time' }}</span>
        </div>
        <div class="card-body">
          <div v-if="subscription.Plan" class="d-flex flex-wrap gap-3 small bg-body-secondary rounded-3 p-2 mb-3">
            <span>Group: <strong>{{ subscription.Plan.NodeGroupName }}</strong></span>
            <span>Original duration: <strong>{{ subscription.Plan.DurationDays || '∞' }} days</strong></span>
            <span>Sale price: <strong>{{ money(subscription.Plan.Price) }} {{ subscription.Plan.Currency }}</strong></span>
          </div>

          <div v-if="edits[subscription.SubscriptionID]" class="row g-2 align-items-end mb-3">
            <div class="col-md-3"><label class="form-label small">Name</label><input v-model="edits[subscription.SubscriptionID].name" class="form-control form-control-sm"></div>
            <div class="col-md-2"><label class="form-label small">Quota (GB)</label><input v-model.number="edits[subscription.SubscriptionID].quota_gb" min="0" step="0.1" type="number" class="form-control form-control-sm"></div>
            <div class="col-md-3"><label class="form-label small">Expiration time</label><input v-model="edits[subscription.SubscriptionID].expires_at" type="datetime-local" class="form-control form-control-sm"></div>
            <div class="col-md-1"><label class="form-label small">Max</label><input v-model.number="edits[subscription.SubscriptionID].max_peers" min="1" type="number" class="form-control form-control-sm"></div>
            <div class="col-md-2"><label class="form-label small">Status</label><select v-model="edits[subscription.SubscriptionID].status" class="form-select form-select-sm"><option value="active">Active</option><option value="disabled">Disabled</option></select></div>
            <div class="col-md-1"><button class="btn btn-sm btn-primary w-100" @click="updateSubscription(subscription.SubscriptionID)"><i class="bi bi-save"></i></button></div>
          </div>

          <div v-if="!subscription.Plan" class="d-flex gap-3 flex-wrap align-items-center border rounded-3 p-2 mb-3">
            <span class="small text-muted">Legacy/custom subscription:</span>
            <label v-for="node in nodes" :key="node.NodeID" class="form-check mb-0" :class="{'opacity-50': node.Status !== 'online'}"><input v-model="selectedNodes[subscription.SubscriptionID]" class="form-check-input" type="checkbox" :disabled="node.Status !== 'online'" :value="node.NodeID"><span class="form-check-label">{{ node.Name }}</span></label>
            <button class="btn btn-sm btn-dark ms-md-auto" :disabled="!(selectedNodes[subscription.SubscriptionID] || []).length" @click="provisionLegacy(subscription.SubscriptionID)">Provision</button>
          </div>

          <div class="d-flex gap-2 justify-content-end mb-3">
            <button class="btn btn-sm btn-outline-info" @click="resetUsage(subscription.SubscriptionID)"><i class="bi bi-arrow-counterclockwise me-2"></i>Reset usage</button>
            <button class="btn btn-sm btn-outline-warning" @click="rotateToken(subscription.SubscriptionID)"><i class="bi bi-arrow-repeat me-2"></i>Rotate link</button>
          </div>

          <div class="table-responsive">
            <table class="table table-sm align-middle mb-0">
              <thead><tr><th>Node</th><th>Location</th><th>Address</th><th>Status</th><th>Usage</th><th></th></tr></thead>
              <tbody>
                <tr v-for="peer in subscription.Peers" :key="peer.SubscriptionPeerID">
                  <td>{{ peer.NodeName }}</td><td>{{ peer.NodeRegion || '—' }}</td><td><code>{{ peer.Address || 'pending' }}</code></td><td>{{ peer.Status }}</td><td>{{ (peer.UsedBytes / 1073741824).toFixed(4) }} GB</td>
                  <td class="text-end"><button v-if="peer.Status === 'active'" class="btn btn-sm btn-outline-warning" @click="peerAction(peer.SubscriptionPeerID, 'DISABLE_PEER')">Disable</button><button v-if="peer.Status === 'disabled'" class="btn btn-sm btn-outline-success" @click="peerAction(peer.SubscriptionPeerID, 'ENABLE_PEER')">Enable</button><button class="btn btn-sm btn-outline-danger ms-1" @click="peerAction(peer.SubscriptionPeerID, 'DELETE_PEER')">Delete</button></td>
                </tr>
                <tr v-if="!subscription.Peers.length"><td colspan="6" class="text-center text-muted py-3">No configurations</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
      <div v-if="!subscriptions.length" class="card"><div class="card-body text-center text-muted py-5">No subscriptions created</div></div>
    </div>
  </div>
</template>
