import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const proxy = process.env.WGD_DEV_PROXY || 'http://127.0.0.1:10086/'

export default defineConfig(({mode}) => {
	if (mode === 'electron'){
		return {
			emptyOutDir: false,
			base: './',
			plugins: [
				vue(),
			],
			resolve: {
				alias: {
					'@': fileURLToPath(new URL('./src', import.meta.url))
				}
			},
			build: {
				target: "es2022",
				outDir: '../../../../WGDashboard-Desktop',
				rollupOptions: {
					output: {
						entryFileNames: `assets/[name]-[hash].js`,
						chunkFileNames: `assets/[name]-[hash].js`,
						assetFileNames: `assets/[name]-[hash].[ext]`
					}
				}
			}
		}
	}

	return {
		base: "./",
		plugins: [
			vue(),
		],
		resolve: {
			alias: {
				'@': fileURLToPath(new URL('./src', import.meta.url))
			}
		},
		server:{
			proxy: {
				'/api': proxy,
				'/fileDownload':proxy
			},
			host: '0.0.0.0'
		},
		build: {
			target: "es2022",
			outDir: '../dist/WGDashboardAdmin',
			rollupOptions: {
				output: {
					entryFileNames: `assets/[name]-[hash].js`,
					chunkFileNames: `assets/[name]-[hash].js`,
					assetFileNames: `assets/[name]-[hash].[ext]`
				}
			}
		}
	}
})
