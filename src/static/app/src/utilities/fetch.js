import {DashboardConfigurationStore} from "@/stores/DashboardConfigurationStore.js";

const REQUEST_TIMEOUT_MS = 20000;
const getHeaders = () => {
	let headers = {
		"Content-Type": "application/json"
	}
	const store = DashboardConfigurationStore();
	const crossServer = store.getActiveCrossServer();
	if (crossServer){
		headers['wg-dashboard-apikey'] = crossServer.apiKey
        if (crossServer.headers){
            for (let header of Object.values(crossServer.headers)){
                if (header.key && header.value && !Object.keys(headers).includes(header.key)){
                    headers[header.key] = header.value
                }
            }
        }
	}


	return headers
}

export const getUrl = (url) => {
	const store = DashboardConfigurationStore();
	const apiKey = store.getActiveCrossServer();
	if (apiKey){
		return `${apiKey.host}${url}`
	}
	if (import.meta.env.MODE === 'development') {
		return url;
	}
	// const appPrefix = window.APP_PREFIX || '';
	return `./.${url}`;
}

const parseResponse = async (response) => {
	const store = DashboardConfigurationStore();
	let payload = null;
	try {
		payload = await response.json();
	} catch (_) {
		// Some proxy and infrastructure errors do not return JSON.
	}
	if (response.ok) return payload;

	const message = payload?.message || response.statusText || 'Request failed';
	if (response.status === 401) {
		store.newMessage('WGDashboard', 'Sign in session ended, please sign in again', 'warning');
	} else {
		store.newMessage('Server', message, 'danger');
	}
	throw new Error(message);
}

const request = async (url, options) => {
	const controller = new AbortController();
	const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
	try {
		return await fetch(url, {...options, signal: controller.signal});
	} finally {
		window.clearTimeout(timeout);
	}
}

export const fetchGet = async (url, params=undefined, callback=undefined) => {
	const urlSearchParams = new URLSearchParams(params);
	try {
		const response = await request(`${getUrl(url)}?${urlSearchParams.toString()}`, {
			headers: getHeaders()
		});
		const payload = await parseResponse(response);
		return callback ? callback(payload) : payload;
	} catch (error) {
		console.log('Error:', error);
		if (error?.name === 'AbortError') {
			DashboardConfigurationStore().newMessage('Server', 'Request timed out', 'danger');
		}
		return undefined;
	}
}

export const fetchPost = async (url, body, callback) => {
	try {
		const response = await request(`${getUrl(url)}`, {
			headers: getHeaders(),
			method: 'POST',
			body: JSON.stringify(body)
		});
		const payload = await parseResponse(response);
		return callback ? callback(payload) : payload;
	} catch (error) {
		console.log('Error:', error);
		if (error?.name === 'AbortError') {
			DashboardConfigurationStore().newMessage('Server', 'Request timed out', 'danger');
		}
		return undefined;
	}
}
