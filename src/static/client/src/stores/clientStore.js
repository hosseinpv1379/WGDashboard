import {defineStore} from "pinia";
import {onMounted, reactive, ref} from "vue";
import {v4} from "uuid"
import dayjs from "dayjs";
import {axiosGet} from "@/utilities/request.js";


export const clientStore = defineStore('clientStore',  {
	state: () => ({
		serverInformation: {},
		notifications: [],
		configurations: [],
		clientProfile: {
			Email: "",
			SignInMethod: "",
			Profile: {}
		}
	}),
	actions: {
		newNotification(content, status){
			this.notifications.push({
				id: v4().toString(),
				status: status,
				content: content,
				time: dayjs(),
				show: true
			})
		},
		async getClientProfile(){
			const data = await axiosGet('/api/settings/getClientProfile')
			if (data){
				this.clientProfile = data.data
			}else{
				this.newNotification("Failed to fetch client profile", "danger")
			}
		},
		async getConfigurations(){
			const data = await axiosGet("/api/configurations")
			const commercial = await axiosGet("/api/subscriptions")
			if (data){
				const commercialConfigurations = []
				if (commercial && commercial.data){
					commercial.data.forEach(subscription => {
						subscription.Peers.filter(peer => peer.Status === 'active' && peer.Configuration).forEach(peer => {
							commercialConfigurations.push({
								name: peer.Name,
								protocol: 'wg',
								data: subscription.UsedGB,
								quota_gb: subscription.QuotaGB,
								quota_exceeded: subscription.Status === 'quota_exceeded',
								expires_at: subscription.ExpiresAt,
								expiry_exceeded: subscription.Status === 'expired',
								jobs: [],
								config: {Name: peer.InterfaceName},
								peer_configuration_data: {
									fileName: peer.Name,
									file: peer.Configuration,
								},
							})
						})
					})
				}
				this.configurations = [...(data.data || []), ...commercialConfigurations]
			}else{
				this.newNotification("Failed to fetch configurations", "danger")
			}
		}
	}
})
