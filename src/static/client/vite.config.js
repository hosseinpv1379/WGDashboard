import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

const proxy = process.env.WGD_DEV_PROXY || 'http://127.0.0.1:10086/'

export default defineConfig({
	plugins: [
		vue(),
		vueDevTools(),
		{
			name: 'rename',
			enforce: 'post',
			generateBundle(options, bundle) {
				bundle['index.html'].fileName = "client.html"
			}
		}
	],
	resolve: {
		alias: {
			'@': fileURLToPath(new URL('./src', import.meta.url))
		},
	},
	server:{
		proxy: {
			'/client': proxy,
		},
		host: '0.0.0.0'
	},
	build: {
		target: "es2022",
		outDir: '../dist/WGDashboardClient',
		rollupOptions: {
			output: {
				entryFileNames: `assets/[name]-[hash].js`,
				chunkFileNames: `assets/[name]-[hash].js`,
				assetFileNames: `assets/[name]-[hash].[ext]`
			}
		}
	},
	base: './'
})
