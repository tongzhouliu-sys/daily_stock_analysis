import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

const frontendDir = __dirname
const repoRoot = path.resolve(frontendDir, '../..')
const packageJson = JSON.parse(
  readFileSync(new URL('./package.json', import.meta.url), 'utf-8'),
) as { version?: string }
const buildTime = new Date().toISOString()
const placeholderVersion = '0.0.0'

const buildInputFiles = [
  'package.json',
  'package-lock.json',
  'vite.config.ts',
  'tsconfig.json',
  'tsconfig.app.json',
  'tsconfig.node.json',
  'eslint.config.js',
  'postcss.config.js',
  'tailwind.config.js',
  'index.html',
]
const buildInputDirectories = ['src', 'public']

const collectFiles = (root: string): string[] => {
  if (!existsSync(root)) {
    return []
  }

  return readdirSync(root, { withFileTypes: true })
    .sort((left, right) => left.name < right.name ? -1 : left.name > right.name ? 1 : 0)
    .flatMap((entry) => {
      const entryPath = path.join(root, entry.name)
      return entry.isDirectory() ? collectFiles(entryPath) : [entryPath]
    })
}

const createSourceFingerprint = () => {
  const inputPaths = [
    ...buildInputFiles.map((fileName) => path.join(frontendDir, fileName)),
    ...buildInputDirectories.flatMap((directoryName) => collectFiles(path.join(frontendDir, directoryName))),
  ]
    .filter((inputPath) => existsSync(inputPath))
    .sort((left, right) => {
      const leftRelative = path.relative(frontendDir, left).replace(/\\/g, '/')
      const rightRelative = path.relative(frontendDir, right).replace(/\\/g, '/')
      return leftRelative < rightRelative ? -1 : leftRelative > rightRelative ? 1 : 0
    })

  const hash = createHash('sha256')
  inputPaths.forEach((inputPath) => {
    hash.update(path.relative(frontendDir, inputPath).replace(/\\/g, '/'))
    hash.update('\0')
    hash.update(readFileSync(inputPath))
    hash.update('\0')
  })
  return hash.digest('hex')
}

const runGit = (args: string[]) => {
  try {
    return execFileSync('git', args, {
      cwd: repoRoot,
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim()
  } catch {
    return ''
  }
}

const normalizeVersion = (value?: string) => {
  const normalized = value?.trim() ?? ''
  return normalized.replace(/^v(?=\d)/, '')
}

export const resolveAppRevision = ({
  explicitRevision,
  checkedOutRevision,
  workflowRevision,
}: {
  explicitRevision?: string
  checkedOutRevision?: string
  workflowRevision?: string
}) => (
  explicitRevision?.trim()
  || checkedOutRevision?.trim()
  || workflowRevision?.trim()
  || 'unknown'
)

const releaseVersion = normalizeVersion(
  process.env.DSA_WEB_VERSION
  || process.env.RELEASE_TAG
  || (process.env.GITHUB_REF_TYPE === 'tag' ? process.env.GITHUB_REF_NAME : ''),
)
const packageVersion = normalizeVersion(packageJson.version)
const gitDescription = normalizeVersion(runGit(['describe', '--tags', '--always', '--dirty']))
const appVersion = releaseVersion
  || (packageVersion !== placeholderVersion ? packageVersion : '')
  || gitDescription
  || placeholderVersion
const appRevision = resolveAppRevision({
  explicitRevision: process.env.DSA_WEB_REVISION,
  checkedOutRevision: runGit(['rev-parse', '--short=12', 'HEAD']),
  workflowRevision: process.env.GITHUB_SHA,
})
const sourceFingerprint = createSourceFingerprint()

const buildMetadataPlugin = (): Plugin => ({
  name: 'dsa-build-metadata',
  generateBundle() {
    this.emitFile({
      type: 'asset',
      fileName: 'build-info.json',
      source: `${JSON.stringify({
        schemaVersion: 1,
        version: appVersion,
        revision: appRevision,
        buildTime,
        sourceFingerprint,
      }, null, 2)}\n`,
    })
  },
})

const vendorChunkByPackage: Record<string, string> = {
  react: 'vendor-react',
  'react-dom': 'vendor-react',
  scheduler: 'vendor-react',
  'react-router': 'vendor-router',
  'react-router-dom': 'vendor-router',
  // T30: 入口与懒加载块共用的小工具包归到首屏 runtime 块，
  // 不再跟着图表库一起进大块（clsx 曾随 recharts 进 vendor-charts）。
  clsx: 'vendor-runtime',
  'tailwind-merge': 'vendor-runtime',
  'lucide-react': 'vendor-icons',
  // T30: 图表块只放 recharts 自身与它的渲染依赖。recharts 的其余传递依赖
  //（`@reduxjs/toolkit`、`react-redux`、`immer`、`es-toolkit`、`use-sync-external-store`、
  // `tiny-invariant` 等）与首屏代码共用，映射进这个块会让入口为它们静态加载
  // 整个图表块；交给 Rollup 按实际引用方放置即可。
  recharts: 'vendor-charts',
  'victory-vendor': 'vendor-charts',
  // Markdown renderer dependencies that are not covered by prefix rules below.
  'react-markdown': 'vendor-markdown',
  unified: 'vendor-markdown',
  vfile: 'vendor-markdown',
  'remove-markdown': 'vendor-markdown',
  bail: 'vendor-markdown',
  'comma-separated-tokens': 'vendor-markdown',
  'decode-named-character-reference': 'vendor-markdown',
  devlop: 'vendor-markdown',
  'html-url-attributes': 'vendor-markdown',
  'property-information': 'vendor-markdown',
  'space-separated-tokens': 'vendor-markdown',
  'trim-lines': 'vendor-markdown',
  'vfile-message': 'vendor-markdown',
  // T18: Export libraries in separate chunk to avoid bloating first-screen vendor bundle
  'html2canvas': 'vendor-export',
  'jspdf': 'vendor-export',
  'docx': 'vendor-export',
}

const vendorChunkByPackagePrefix: Array<[string, string]> = [
  ['d3-', 'vendor-charts'],
  ['remark-', 'vendor-markdown'],
  ['micromark', 'vendor-markdown'],
  ['mdast-util-', 'vendor-markdown'],
  ['hast-util-', 'vendor-markdown'],
  ['unist-util-', 'vendor-markdown'],
]

const getVendorPackageName = (id: string): string | undefined => {
  const normalizedId = id.replace(/\\/g, '/')
  const marker = '/node_modules/'
  const markerIndex = normalizedId.lastIndexOf(marker)
  if (markerIndex === -1) {
    return undefined
  }

  const packagePath = normalizedId.slice(markerIndex + marker.length)
  const [firstSegment, secondSegment] = packagePath.split('/')
  if (!firstSegment) {
    return undefined
  }

  if (firstSegment.startsWith('@')) {
    return secondSegment ? `${firstSegment}/${secondSegment}` : undefined
  }

  return firstSegment
}

// T30: 首屏 runtime 块。虚拟模块（id 以 \0 开头，例如 `vite/preload-helper`、
// `commonjsHelpers`）被入口与所有懒加载块引用，必须单独成块。
const FIRST_SCREEN_RUNTIME_CHUNK = 'vendor-runtime'

const isViteRuntimeModule = (id: string): boolean => {
  const normalizedId = id.replace(/\\/g, '/')
  return (
    normalizedId.startsWith('\0')
    || normalizedId.includes('vite/preload-helper')
    || normalizedId.includes('commonjsHelpers')
  )
}

const getVendorChunkName = (id: string): string | undefined => {
  // T30: Vite 的 `__vitePreload` 辅助函数等虚拟模块必须单独成块。它们被入口和
  // 所有懒加载块引用，落进最大的依赖块时，入口就必须静态加载整个大块。
  if (isViteRuntimeModule(id)) {
    return FIRST_SCREEN_RUNTIME_CHUNK
  }

  const packageName = getVendorPackageName(id)
  if (!packageName) {
    return undefined
  }

  return (
    vendorChunkByPackage[packageName]
    ?? vendorChunkByPackagePrefix.find(([prefix]) => packageName.startsWith(prefix))?.[1]
  )
}

// https://vite.dev/config/
export default defineConfig({
  define: {
    __APP_PACKAGE_VERSION__: JSON.stringify(appVersion),
    __APP_REVISION__: JSON.stringify(appRevision),
    __APP_BUILD_TIME__: JSON.stringify(buildTime),
  },
  plugins: [
    buildMetadataPlugin(),
    tailwindcss(),
    react({
      babel: {
        plugins: [['babel-plugin-react-compiler']],
      },
    }),
  ],
  server: {
    host: '0.0.0.0',  // 允许公网访问
    port: 5173,       // 默认端口
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    // 打包输出到项目根目录的 static 文件夹
    outDir: path.resolve(__dirname, '../../static'),
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: getVendorChunkName,
      },
    },
  },
})
