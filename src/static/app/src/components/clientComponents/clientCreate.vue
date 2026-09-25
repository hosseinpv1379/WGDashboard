<script setup lang="ts">
import {reactive, ref} from "vue";
import LocaleText from "@/components/text/localeText.vue";
import {fetchPost} from "@/utilities/fetch.js";
import {DashboardConfigurationStore} from "@/stores/DashboardConfigurationStore.js";

const emit = defineEmits(['close', 'created'])
const dashboardStore = DashboardConfigurationStore()
const saving = ref(false)
const form = reactive({
	Email: '',
	Name: '',
	Password: '',
	ConfirmPassword: '',
})

const createClient = async () => {
	saving.value = true
	await fetchPost('/api/clients/createClient', form, (res) => {
		if (res.status){
			dashboardStore.newMessage('Server', 'Client created successfully', 'success')
			emit('created')
		}else{
			dashboardStore.newMessage('Server', res.message, 'danger')
		}
	})
	saving.value = false
}
</script>

<template>
	<div class="position-absolute w-100 h-100 top-0 start-0 rounded-3 d-flex p-2"
	     style="background-color: #00000070; z-index: 9999">
		<div class="card m-auto rounded-3 shadow" style="width: 520px; max-width: 100%">
			<div class="card-header bg-transparent d-flex align-items-center border-0 p-4 pb-2">
				<h4 class="mb-0"><LocaleText t="Create Client"></LocaleText></h4>
				<button type="button" class="btn-close ms-auto" @click="emit('close')"></button>
			</div>
			<form class="card-body px-4 d-flex flex-column gap-3" @submit.prevent="createClient">
				<div>
					<label for="new_client_email" class="form-label"><LocaleText t="Email"></LocaleText></label>
					<input id="new_client_email" v-model.trim="form.Email" type="email" required
					       :disabled="saving" class="form-control rounded-3" autocomplete="username">
				</div>
				<div>
					<label for="new_client_name" class="form-label"><LocaleText t="Client Name"></LocaleText></label>
					<input id="new_client_name" v-model.trim="form.Name" type="text"
					       :disabled="saving" class="form-control rounded-3">
				</div>
				<div>
					<label for="new_client_password" class="form-label"><LocaleText t="Password"></LocaleText></label>
					<input id="new_client_password" v-model="form.Password" type="password" required minlength="8"
					       :disabled="saving" class="form-control rounded-3" autocomplete="new-password">
				</div>
				<div>
					<label for="new_client_password_confirm" class="form-label"><LocaleText t="Confirm Password"></LocaleText></label>
					<input id="new_client_password_confirm" v-model="form.ConfirmPassword" type="password" required minlength="8"
					       :disabled="saving" class="form-control rounded-3" autocomplete="new-password">
				</div>
				<div class="d-flex">
					<button class="btn btn-dark btn-brand rounded-3 ms-auto px-3" type="submit"
					        :disabled="saving || form.Password !== form.ConfirmPassword">
						<i class="bi bi-person-plus-fill me-2"></i>
						<LocaleText :t="saving ? 'Creating...' : 'Create Client'"></LocaleText>
					</button>
				</div>
			</form>
		</div>
	</div>
</template>
