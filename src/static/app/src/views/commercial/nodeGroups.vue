<script setup>
import {onMounted, reactive, ref} from 'vue'
import {fetchGet, fetchPost} from '@/utilities/fetch.js'
import {DashboardConfigurationStore} from '@/stores/DashboardConfigurationStore.js'

const dashboardStore = DashboardConfigurationStore()
const nodes = ref([])
const groups = ref([])
const loading = ref(false)
const form = reactive({name: '', description: '', node_ids: [], status: 'active'})

const load = async () => {
  loading.value = true
  await fetchGet('/api/commercial/nodes', {}, (response) => { nodes.value = response.data || [] })
  await fetchGet('/api/commercial/node-groups', {}, (response) => {
    groups.value = (response.data || []).map((group) => ({...group, NodeIDs: [...(group.NodeIDs || [])]}))
  })
  loading.value = false
}

const createGroup = async () => {
  await fetchPost('/api/commercial/node-groups', form, (response) => {
    if (response.status) {
      Object.assign(form, {name: '', description: '', node_ids: [], status: 'active'})
      dashboardStore.newMessage('Node Groups', 'Node group created', 'success')
    }
  })
  await load()
}

const updateGroup = async (group) => {
  await fetchPost(`/api/commercial/node-groups/${group.NodeGroupID}`, {
    name: group.Name,
    description: group.Description,
    node_ids: group.NodeIDs,
    status: group.Status,
  }, (response) => {
    if (response.status) dashboardStore.newMessage('Node Groups', 'Node group updated', 'success')
  })
  await load()
}

onMounted(load)
</script>

<template>
  <div>
    <div class="alert alert-info rounded-3">
      A node group defines the locations delivered by a package. For example, add Germany and Finland to one group to sell a two-location package.
    </div>

    <div class="card rounded-3 mb-4">
      <div class="card-header bg-transparent"><strong>Create node group</strong></div>
      <form class="card-body row g-3" @submit.prevent="createGroup">
        <div class="col-md-4"><label class="form-label">Name</label><input v-model.trim="form.name" required class="form-control" placeholder="Europe Basic"></div>
        <div class="col-md-6"><label class="form-label">Description</label><input v-model.trim="form.description" class="form-control" placeholder="Germany + Finland"></div>
        <div class="col-md-2"><label class="form-label">Status</label><select v-model="form.status" class="form-select"><option value="active">Active</option><option value="disabled">Disabled</option></select></div>
        <div class="col-12">
          <label class="form-label">Nodes / locations</label>
          <div class="d-flex flex-wrap gap-3 border rounded-3 p-3">
            <label v-for="node in nodes" :key="node.NodeID" class="form-check mb-0">
              <input v-model="form.node_ids" class="form-check-input" type="checkbox" :value="node.NodeID">
              <span class="form-check-label">{{ node.Name }} <span class="text-muted">({{ node.Region || 'No location' }})</span></span>
            </label>
            <span v-if="!nodes.length" class="text-muted">Create at least one node first.</span>
          </div>
        </div>
        <div class="col-12 text-end"><button class="btn btn-dark" :disabled="!form.node_ids.length" type="submit"><i class="bi bi-plus-lg me-2"></i>Create group</button></div>
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
              <label class="form-label small">Included nodes</label>
              <div class="d-flex flex-wrap gap-3 border rounded-3 p-3">
                <label v-for="node in nodes" :key="node.NodeID" class="form-check mb-0">
                  <input v-model="group.NodeIDs" class="form-check-input" type="checkbox" :value="node.NodeID">
                  <span class="form-check-label">{{ node.Name }} <span class="text-muted">({{ node.Region || '—' }})</span></span>
                </label>
              </div>
            </div>
            <div class="col-12 d-flex align-items-center">
              <small class="text-muted">{{ group.NodeCount }} location(s) · <code>{{ group.NodeGroupID }}</code></small>
              <button class="btn btn-primary ms-auto" :disabled="!group.NodeIDs.length" @click="updateGroup(group)"><i class="bi bi-save me-2"></i>Save</button>
            </div>
          </div>
        </div>
      </div>
      <div v-if="!groups.length" class="col-12"><div class="card"><div class="card-body text-center text-muted py-5">No node groups created</div></div></div>
    </div>
  </div>
</template>
