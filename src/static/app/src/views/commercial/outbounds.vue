<script setup>
import {computed, onMounted, reactive, ref, watch} from 'vue'
import {fetchGet, fetchPost} from '@/utilities/fetch.js'
import {DashboardConfigurationStore} from '@/stores/DashboardConfigurationStore.js'

const dashboardStore = DashboardConfigurationStore()
const nodes = ref([])
const outbounds = ref([])
const edits = reactive({})
const loading = ref(false)
const form = reactive({
  node_id: '', name: '', interface: 'wgo0', source_interface: '',
  source_address_pool: '', configuration: '',
})

const selectedNode = computed(() => nodes.value.find((node) => node.NodeID === form.node_id))
const sourceInterfaces = computed(() => selectedNode.value?.Interfaces || [])

watch(() => form.node_id, () => {
  const first = sourceInterfaces.value[0]
  form.source_interface = first?.InterfaceName || 'wg0'
  form.source_address_pool = first?.AddressPool || ''
})

watch(() => form.source_interface, () => {
  const item = sourceInterfaces.value.find((entry) => entry.InterfaceName === form.source_interface)
  if (item?.AddressPool) form.source_address_pool = item.AddressPool
})

const load = async () => {
  loading.value = true
  await fetchGet('/api/commercial/nodes', {}, (response) => { nodes.value = response.data || [] })
  await fetchGet('/api/commercial/outbounds', {}, (response) => {
    outbounds.value = response.data || []
    outbounds.value.forEach((item) => {
      edits[item.OutboundID] = {
        name: item.Name,
        source_interface: item.SourceInterface,
        source_address_pool: item.SourceAddressPool,
        configuration: '',
      }
    })
  })
  loading.value = false
}

const createOutbound = async () => {
  await fetchPost('/api/commercial/outbounds', form, (response) => {
    if (!response.status) return
    dashboardStore.newMessage('Outbounds', 'Outbound apply job queued', 'success')
    Object.assign(form, {
      node_id: '', name: '', interface: 'wgo0', source_interface: '',
      source_address_pool: '', configuration: '',
    })
  })
  await load()
}

const save = async (item) => {
  await fetchPost(`/api/commercial/outbounds/${item.OutboundID}`, edits[item.OutboundID], (response) => {
    if (response.status) dashboardStore.newMessage('Outbounds', 'Outbound update queued', 'success')
  })
  await load()
}

const action = async (item, operation) => {
  if (operation === 'REMOVE_OUTBOUND' && !window.confirm(`Disable outbound ${item.Name}?`)) return
  await fetchPost(`/api/commercial/outbounds/${item.OutboundID}/action`, {operation}, (response) => {
    if (response.status) dashboardStore.newMessage('Outbounds', operation === 'APPLY_OUTBOUND' ? 'Outbound apply queued' : 'Outbound removal queued', 'success')
  })
  await load()
}

onMounted(load)
</script>

<template>
  <div>
    <div class="alert alert-info rounded-3">
      An outbound routes only the selected source interface pool through another WireGuard tunnel. Operator hooks in pasted configurations are removed, and policy routing prevents the panel itself from losing connectivity.
    </div>

    <div class="card rounded-3 mb-4">
      <div class="card-header bg-transparent"><strong>Add WireGuard outbound</strong></div>
      <form class="card-body row g-3" @submit.prevent="createOutbound">
        <div class="col-md-4"><label class="form-label">Node</label><select v-model="form.node_id" required class="form-select"><option value="" disabled>Select node</option><option v-for="node in nodes" :key="node.NodeID" :value="node.NodeID">{{ node.Name }} — {{ node.Region || 'No location' }}</option></select></div>
        <div class="col-md-4"><label class="form-label">Name</label><input v-model.trim="form.name" required class="form-control" placeholder="Germany via upstream"></div>
        <div class="col-md-4"><label class="form-label">Outbound interface</label><input v-model.trim="form.interface" required pattern="[A-Za-z0-9_=+.\-]{1,64}" class="form-control" placeholder="wgo0"><div class="form-text">Must not match a server interface.</div></div>
        <div class="col-md-6"><label class="form-label">Source interface</label><select v-model="form.source_interface" required class="form-select"><option v-if="!sourceInterfaces.length" value="wg0">wg0 (not reported yet)</option><option v-for="item in sourceInterfaces" :key="item.InterfaceName" :value="item.InterfaceName">{{ item.InterfaceName }} — {{ item.AddressPool }}</option></select></div>
        <div class="col-md-6"><label class="form-label">Source address pool</label><input v-model.trim="form.source_address_pool" required class="form-control" placeholder="10.88.0.0/24"></div>
        <div class="col-12"><label class="form-label">Upstream WireGuard configuration</label><textarea v-model="form.configuration" required rows="12" class="form-control font-monospace" placeholder="[Interface]&#10;PrivateKey = ...&#10;Address = ...&#10;&#10;[Peer]&#10;PublicKey = ...&#10;Endpoint = ...&#10;AllowedIPs = 0.0.0.0/0"></textarea><div class="form-text">The configuration must include <code>AllowedIPs = 0.0.0.0/0</code>. Private keys are encrypted in the panel database.</div></div>
        <div class="col-12 text-end"><button class="btn btn-dark" type="submit"><i class="bi bi-diagram-3 me-2"></i>Create and apply</button></div>
      </form>
    </div>

    <div class="d-flex align-items-center mb-3"><h5 class="mb-0">Outbounds</h5><button class="btn btn-sm btn-outline-secondary ms-auto" :disabled="loading" @click="load"><i class="bi bi-arrow-clockwise"></i></button></div>
    <div class="row g-3">
      <div v-for="item in outbounds" :key="item.OutboundID" class="col-xl-6">
        <div class="card rounded-3 h-100">
          <div class="card-header bg-transparent d-flex align-items-center gap-2"><strong>{{ item.NodeName }} / {{ item.InterfaceName }}</strong><span class="badge ms-auto" :class="item.Status === 'active' ? 'text-bg-success' : item.Status === 'error' ? 'text-bg-danger' : 'text-bg-secondary'">{{ item.Status }}</span></div>
          <div v-if="edits[item.OutboundID]" class="card-body row g-3">
            <div class="col-md-6"><label class="form-label small">Name</label><input v-model.trim="edits[item.OutboundID].name" class="form-control"></div>
            <div class="col-md-6"><label class="form-label small">Source interface</label><input v-model.trim="edits[item.OutboundID].source_interface" class="form-control"></div>
            <div class="col-12"><label class="form-label small">Source pool</label><input v-model.trim="edits[item.OutboundID].source_address_pool" class="form-control"></div>
            <div class="col-12"><label class="form-label small">Replace configuration <span class="text-muted">(leave empty to keep current secret)</span></label><textarea v-model="edits[item.OutboundID].configuration" rows="6" class="form-control font-monospace"></textarea></div>
            <div v-if="item.LastError" class="col-12"><div class="alert alert-danger py-2 mb-0">{{ item.LastError }}</div></div>
            <div class="col-12 d-flex gap-2">
              <button class="btn btn-outline-danger" @click="action(item, 'REMOVE_OUTBOUND')">Disable</button>
              <button class="btn btn-outline-success" @click="action(item, 'APPLY_OUTBOUND')">Apply</button>
              <button class="btn btn-primary ms-auto" @click="save(item)"><i class="bi bi-save me-2"></i>Save and apply</button>
            </div>
          </div>
        </div>
      </div>
      <div v-if="!outbounds.length" class="col-12"><div class="card"><div class="card-body text-center text-muted py-5">No outbounds configured</div></div></div>
    </div>
  </div>
</template>
