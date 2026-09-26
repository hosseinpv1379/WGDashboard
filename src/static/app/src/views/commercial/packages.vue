<script setup>
import {onMounted, reactive, ref} from 'vue'
import {fetchGet, fetchPost} from '@/utilities/fetch.js'
import {DashboardConfigurationStore} from '@/stores/DashboardConfigurationStore.js'

const dashboardStore = DashboardConfigurationStore()
const groups = ref([])
const packages = ref([])
const loading = ref(false)
const emptyForm = () => ({name: '', description: '', quota_gb: 30, duration_days: 30, price: 0, currency: 'IRT', node_group_id: '', status: 'active'})
const form = reactive(emptyForm())

const load = async () => {
  loading.value = true
  await fetchGet('/api/commercial/node-groups', {}, (response) => { groups.value = response.data || [] })
  await fetchGet('/api/commercial/packages', {}, (response) => { packages.value = response.data || [] })
  loading.value = false
}

const createPackage = async () => {
  await fetchPost('/api/commercial/packages', form, (response) => {
    if (response.status) {
      Object.assign(form, emptyForm())
      dashboardStore.newMessage('Packages', 'Package created', 'success')
    }
  })
  await load()
}

const updatePackage = async (item) => {
  await fetchPost(`/api/commercial/packages/${item.PackageID}`, {
    name: item.Name,
    description: item.Description,
    quota_gb: item.QuotaGB,
    duration_days: item.DurationDays,
    price: item.Price,
    currency: item.Currency,
    node_group_id: item.NodeGroupID,
    status: item.Status,
  }, (response) => {
    if (response.status) dashboardStore.newMessage('Packages', 'Package updated; existing subscriptions are syncing if the node group changed', 'success')
  })
  await load()
}

const money = (value) => new Intl.NumberFormat().format(Number(value || 0))

onMounted(load)
</script>

<template>
  <div>
    <div class="card rounded-3 mb-4">
      <div class="card-header bg-transparent"><strong>Create package</strong></div>
      <form class="card-body row g-3" @submit.prevent="createPackage">
        <div class="col-md-4"><label class="form-label">Package name</label><input v-model.trim="form.name" required class="form-control" placeholder="30 GiB / 30 days"></div>
        <div class="col-md-4"><label class="form-label">Node group</label><select v-model="form.node_group_id" required class="form-select"><option value="" disabled>Select locations</option><option v-for="group in groups" :key="group.NodeGroupID" :disabled="group.Status !== 'active'" :value="group.NodeGroupID">{{ group.Name }} — {{ group.InterfaceCount }} configuration(s)</option></select></div>
        <div class="col-md-2"><label class="form-label">Data (GiB)</label><input v-model.number="form.quota_gb" min="0" step="0.1" required type="number" class="form-control"></div>
        <div class="col-md-2"><label class="form-label">Duration (days)</label><input v-model.number="form.duration_days" min="0" required type="number" class="form-control"></div>
        <div class="col-md-5"><label class="form-label">Description</label><input v-model.trim="form.description" class="form-control" placeholder="Suitable for two locations"></div>
        <div class="col-md-3"><label class="form-label">Price</label><input v-model.number="form.price" min="0" step="0.01" required type="number" class="form-control"></div>
        <div class="col-md-2"><label class="form-label">Currency</label><input v-model.trim="form.currency" maxlength="16" required class="form-control" placeholder="IRT"></div>
        <div class="col-md-2"><label class="form-label">Status</label><select v-model="form.status" class="form-select"><option value="active">Active</option><option value="disabled">Disabled</option></select></div>
        <div class="col-12 text-end"><button class="btn btn-dark" :disabled="!groups.length" type="submit"><i class="bi bi-plus-lg me-2"></i>Create package</button></div>
      </form>
    </div>

    <div class="d-flex align-items-center mb-3"><h5 class="mb-0">Packages</h5><button class="btn btn-sm btn-outline-secondary ms-auto" :disabled="loading" @click="load"><i class="bi bi-arrow-clockwise"></i></button></div>
    <div class="row g-3">
      <div v-for="item in packages" :key="item.PackageID" class="col-xxl-4 col-lg-6">
        <div class="card rounded-3 h-100">
          <div class="card-header bg-transparent d-flex align-items-center gap-2">
            <strong>{{ item.Name }}</strong>
            <span class="badge ms-auto" :class="item.Status === 'active' ? 'text-bg-success' : 'text-bg-secondary'">{{ item.Status }}</span>
          </div>
          <div class="card-body row g-2">
            <div class="col-12"><label class="form-label small">Name</label><input v-model.trim="item.Name" class="form-control form-control-sm"></div>
            <div class="col-6"><label class="form-label small">Data (GiB)</label><input v-model.number="item.QuotaGB" min="0" step="0.1" type="number" class="form-control form-control-sm"></div>
            <div class="col-6"><label class="form-label small">Days</label><input v-model.number="item.DurationDays" min="0" type="number" class="form-control form-control-sm"></div>
            <div class="col-6"><label class="form-label small">Price</label><input v-model.number="item.Price" min="0" step="0.01" type="number" class="form-control form-control-sm"></div>
            <div class="col-3"><label class="form-label small">Currency</label><input v-model.trim="item.Currency" class="form-control form-control-sm"></div>
            <div class="col-3"><label class="form-label small">Status</label><select v-model="item.Status" class="form-select form-select-sm"><option value="active">Active</option><option value="disabled">Disabled</option></select></div>
            <div class="col-12"><label class="form-label small">Node group</label><select v-model="item.NodeGroupID" class="form-select form-select-sm"><option v-for="group in groups" :key="group.NodeGroupID" :value="group.NodeGroupID">{{ group.Name }} — {{ group.InterfaceCount }} configuration(s)</option></select></div>
            <div class="col-12"><label class="form-label small">Description</label><textarea v-model.trim="item.Description" rows="2" class="form-control form-control-sm"></textarea></div>
            <div class="col-12 d-flex align-items-center mt-3"><span class="small text-muted">{{ money(item.Price) }} {{ item.Currency }} · {{ item.InterfaceCount }} config(s) on {{ item.NodeCount }} node(s)</span><button class="btn btn-sm btn-primary ms-auto" @click="updatePackage(item)"><i class="bi bi-save me-2"></i>Save</button></div>
          </div>
        </div>
      </div>
      <div v-if="!packages.length" class="col-12"><div class="card"><div class="card-body text-center text-muted py-5">No packages created</div></div></div>
    </div>
  </div>
</template>
