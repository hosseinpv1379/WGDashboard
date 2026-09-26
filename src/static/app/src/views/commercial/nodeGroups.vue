<script setup>
import {computed, onMounted, reactive, ref} from 'vue'
import {fetchGet, fetchPost} from '@/utilities/fetch.js'
import {DashboardConfigurationStore} from '@/stores/DashboardConfigurationStore.js'

const dashboardStore = DashboardConfigurationStore()
const nodes = ref([])
const groups = ref([])
const loading = ref(false)
const form = reactive({name: '', description: '', target_keys: [], status: 'active'})
const targetOptions = computed(() => nodes.value.flatMap((node) => {
  const interfaces = node.Interfaces?.length ? node.Interfaces : [{InterfaceName: 'wg0', Status: 'unknown'}]
  return interfaces.map((item) => ({
    key: `${node.NodeID}::${item.InterfaceName}`,
    nodeID: node.NodeID,
    interfaceName: item.InterfaceName,
    label: `${node.Name} / ${item.InterfaceName}`,
    location: node.Region || 'No location',
    status: item.Status || node.Status,
  }))
}))
const targetsFromKeys = (keys) => (keys || []).map((key) => {
  const [node_id, interfaceName] = key.split('::')
  return {node_id, interface: interfaceName}
})

const load = async () => {
  loading.value = true
  await fetchGet('/api/commercial/nodes', {}, (response) => { nodes.value = response.data || [] })
  await fetchGet('/api/commercial/node-groups', {}, (response) => {
    groups.value = (response.data || []).map((group) => ({...group, TargetKeys: [...(group.TargetKeys || [])]}))
  })
  loading.value = false
}

const createGroup = async () => {
  await fetchPost('/api/commercial/node-groups', {
    name: form.name, description: form.description, status: form.status,
    targets: targetsFromKeys(form.target_keys),
  }, (response) => {
    if (response.status) {
      Object.assign(form, {name: '', description: '', target_keys: [], status: 'active'})
      dashboardStore.newMessage('Node Groups', 'Node group created', 'success')
    }
  })
  await load()
}

const updateGroup = async (group) => {
  await fetchPost(`/api/commercial/node-groups/${group.NodeGroupID}`, {
    name: group.Name,
    description: group.Description,
    targets: targetsFromKeys(group.TargetKeys),
    status: group.Status,
  }, (response) => {
    if (response.status) dashboardStore.newMessage('Node Groups', 'Node group updated; existing subscriptions are syncing', 'success')
  })
  await load()
}

onMounted(load)
</script>

<template>
  <div>
    <div class="alert alert-info rounded-3">
      A node group defines the locations delivered by a package. Adding or removing an interface automatically updates every existing subscription sold from this group.
    </div>

    <div class="card rounded-3 mb-4">
      <div class="card-header bg-transparent"><strong>Create node group</strong></div>
      <form class="card-body row g-3" @submit.prevent="createGroup">
        <div class="col-md-4"><label class="form-label">Name</label><input v-model.trim="form.name" required class="form-control" placeholder="Europe Basic"></div>
        <div class="col-md-6"><label class="form-label">Description</label><input v-model.trim="form.description" class="form-control" placeholder="Germany + Finland"></div>
        <div class="col-md-2"><label class="form-label">Status</label><select v-model="form.status" class="form-select"><option value="active">Active</option><option value="disabled">Disabled</option></select></div>
        <div class="col-12">
          <label class="form-label">Node interfaces / locations</label>
          <div class="d-flex flex-wrap gap-3 border rounded-3 p-3">
            <label v-for="target in targetOptions" :key="target.key" class="form-check mb-0">
              <input v-model="form.target_keys" class="form-check-input" type="checkbox" :value="target.key">
              <span class="form-check-label">{{ target.label }} <span class="text-muted">({{ target.location }})</span></span>
            </label>
            <span v-if="!nodes.length" class="text-muted">Create at least one node first.</span>
          </div>
        </div>
        <div class="col-12 text-end"><button class="btn btn-dark" :disabled="!form.target_keys.length" type="submit"><i class="bi bi-plus-lg me-2"></i>Create group</button></div>
      </form>
    </div>

    <div class="d-flex align-items-center mb-3"><h5 class="mb-0">Node groups</h5><button class="btn btn-sm btn-outline-secondary ms-auto" :disabled="loading" @click="load"><i class="bi bi-arrow-clockwise"></i></button></div>
    <div class="row g-3">
      <div v-for="group in groups" :key="group.NodeGroupID" class="col-xl-6">
        <div class="card rounded-3 h-100">
          <div class="card-body row g-3">
            <div class="col-md-8"><label class="form-label small">Name</label><input v-model.trim="group.Name" class="form-control"></div>
            <div class="col-md-4"><label class="form-label small">Status</label><select v-model="group.Status" class="form-select"><option value="active">Active</option><option value="disabled">Disabled</option></select></div>
            <div class="col-12"><label class="form-label small">Description</label><input v-model.trim="group.Description" class="form-control"></div>
            <div class="col-12">
              <label class="form-label small">Included node interfaces</label>
              <div class="d-flex flex-wrap gap-3 border rounded-3 p-3">
                <label v-for="target in targetOptions" :key="target.key" class="form-check mb-0">
                  <input v-model="group.TargetKeys" class="form-check-input" type="checkbox" :value="target.key">
                  <span class="form-check-label">{{ target.label }} <span class="text-muted">({{ target.location }})</span></span>
                </label>
              </div>
            </div>
            <div class="col-12 d-flex align-items-center">
              <small class="text-muted">{{ group.NodeCount }} node(s) · {{ group.InterfaceCount }} configuration(s) · <code>{{ group.NodeGroupID }}</code></small>
              <button class="btn btn-primary ms-auto" :disabled="!group.TargetKeys.length" @click="updateGroup(group)"><i class="bi bi-save me-2"></i>Save</button>
            </div>
          </div>
        </div>
      </div>
      <div v-if="!groups.length" class="col-12"><div class="card"><div class="card-body text-center text-muted py-5">No node groups created</div></div></div>
    </div>
  </div>
</template>
