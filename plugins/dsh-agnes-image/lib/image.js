/**
 * Image format helpers: magic-byte sniffing, dimension probing, data URIs,
 * and deterministic output file names. Node built-ins only.
 */

/** Media types accepted by the attachment store and written to disk. */
export const SUPPORTED_IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']

const PNG_SIGNATURE = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])

/** Identify a supported image format from its first bytes. */
export function sniffImageMediaType(bytes) {
  if (!(bytes instanceof Uint8Array) || bytes.byteLength < 12) return undefined
  if (bytes.subarray(0, 8).equals(PNG_SIGNATURE)) return 'image/png'
  if (bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) return 'image/jpeg'
  const ascii = (offset, value) => Buffer.from(bytes.subarray(offset, offset + value.length)).toString('latin1') === value
  if (ascii(0, 'GIF87a') || ascii(0, 'GIF89a')) return 'image/gif'
  if (ascii(0, 'RIFF') && ascii(8, 'WEBP')) return 'image/webp'
  return undefined
}

/** File extension for a supported media type. */
export function extensionFor(mediaType) {
  switch (mediaType) {
    case 'image/jpeg': return '.jpg'
    case 'image/webp': return '.webp'
    case 'image/gif': return '.gif'
    default: return '.png'
  }
}

/** Decode a supported image's intrinsic size, or `undefined` when it cannot be read. */
export function imageDimensions(bytes, mediaType = sniffImageMediaType(bytes)) {
  try {
    if (!(bytes instanceof Uint8Array) || bytes.byteLength < 16) return undefined
    if (mediaType === 'image/png') {
      return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) }
    }
    if (mediaType === 'image/gif') {
      return { width: bytes.readUInt16LE(6), height: bytes.readUInt16LE(8) }
    }
    if (mediaType === 'image/webp') {
      return webpDimensions(Buffer.from(bytes))
    }
    if (mediaType === 'image/jpeg') {
      return jpegDimensions(Buffer.from(bytes))
    }
  } catch {
    // Truncated or unusual file: the attachment service remains authoritative.
  }
  return undefined
}

function jpegDimensions(buffer) {
  let offset = 2
  while (offset + 9 < buffer.length) {
    if (buffer[offset] !== 0xff) return undefined
    const marker = buffer[offset + 1]
    if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
      offset += 2
      continue
    }
    const length = buffer.readUInt16BE(offset + 2)
    if (length < 2) return undefined
    const isStartOfFrame =
      (marker >= 0xc0 && marker <= 0xc3) ||
      (marker >= 0xc5 && marker <= 0xc7) ||
      (marker >= 0xc9 && marker <= 0xcb) ||
      (marker >= 0xcd && marker <= 0xcf)
    if (isStartOfFrame) {
      return { width: buffer.readUInt16BE(offset + 7), height: buffer.readUInt16BE(offset + 5) }
    }
    offset += 2 + length
  }
  return undefined
}

function webpDimensions(buffer) {
  if (buffer.length < 30) return undefined
  const chunk = buffer.toString('latin1', 12, 16)
  if (chunk === 'VP8X') {
    const width = 1 + buffer.readUIntLE(24, 3)
    const height = 1 + buffer.readUIntLE(27, 3)
    return { width, height }
  }
  if (chunk === 'VP8 ') {
    return { width: buffer.readUInt16LE(26) & 0x3fff, height: buffer.readUInt16LE(28) & 0x3fff }
  }
  if (chunk === 'VP8L') {
    const bits = buffer.readUInt32LE(21)
    return { width: (bits & 0x3fff) + 1, height: ((bits >> 14) & 0x3fff) + 1 }
  }
  return undefined
}

/** Encode bytes as an RFC 2397 data URI the API accepts for image-to-image input. */
export function toDataUri(bytes, mediaType) {
  const type = mediaType ?? sniffImageMediaType(bytes) ?? 'application/octet-stream'
  return `data:${type};base64,${Buffer.from(bytes).toString('base64')}`
}

/** Two-digit zero pad. */
function pad(value) {
  return String(value).padStart(2, '0')
}

/**
 * Deterministic, collision-resistant default file name:
 * `agnes-20261026-225530-a1b2c3.png`.
 */
export function defaultImageFileName(date = new Date(), suffix = '') {
  const stamp = `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
  return `agnes-${stamp}${suffix === '' ? '' : `-${suffix}`}.png`
}

/** Remove path separators and control characters from a model-supplied base name. */
export function safeBaseName(value, fallback = 'agnes-image') {
  const cleaned = String(value ?? '')
    .replace(/[\\/:*?"<>|\u0000-\u001f]/gu, '-')
    .replace(/\s+/gu, '-')
    .replace(/^[.-]+|[.-]+$/gu, '')
    .slice(0, 80)
  return cleaned.length === 0 ? fallback : cleaned
}

/** Human-readable byte count. */
export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return `${bytes} B`
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}
