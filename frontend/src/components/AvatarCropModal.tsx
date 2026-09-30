import React, { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import './AvatarCropModal.css'

type Props = {
  file: File
  onConfirm: (blob: Blob) => void
  onCancel: () => void
}

const SIZE = 280              // display canvas size
const OUTPUT_SIZE = 256       // output image size
const MAX_ZOOM_FACTOR = 2.5   // max zoom = coverZoom * this

const AvatarCropModal: React.FC<Props> = ({ file, onConfirm, onCancel }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const [imgLoaded, setImgLoaded] = useState(false)

  // Zoom range anchored to actual image dimensions
  const [fitZoom, setFitZoom] = useState(1)     // image larger side = circle diameter
  const [coverZoom, setCoverZoom] = useState(1) // image smaller side = circle diameter
  const [maxZoom, setMaxZoom] = useState(1)
  const [defaultPct, setDefaultPct] = useState(0) // slider position (%) for coverZoom

  const [sliderPct, setSliderPct] = useState(0)  // 0-100
  const [zoom, setZoom] = useState(1)
  const [offset, setOffset] = useState({ x: 0, y: 0 })
  const [dragging, setDragging] = useState(false)
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 })

  // Load image and compute zoom scale
  useEffect(() => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      imgRef.current = img
      URL.revokeObjectURL(url)

      const fz = SIZE / Math.max(img.width, img.height)
      const cz = SIZE / Math.min(img.width, img.height)
      const mz = cz * MAX_ZOOM_FACTOR

      setFitZoom(fz)
      setCoverZoom(cz)
      setMaxZoom(mz)

      // Default slider position = coverZoom on the fitZoom..maxZoom range
      const dpct = mz > fz ? Math.round(((cz - fz) / (mz - fz)) * 100) : 0
      setDefaultPct(dpct)

      setSliderPct(dpct)
      setZoom(cz)
      setOffset({ x: 0, y: 0 })
      setImgLoaded(true)
    }
    img.src = url
    return () => URL.revokeObjectURL(url)
  }, [file])

  // Update zoom when slider changes
  const handleSliderChange = (pct: number) => {
    setSliderPct(pct)
    const z = fitZoom + (maxZoom - fitZoom) * (pct / 100)
    setZoom(z)
  }

  // Draw canvas
  const draw = useCallback(() => {
    const canvas = canvasRef.current
    const img = imgRef.current
    if (!canvas || !img) return

    const ctx = canvas.getContext('2d')!
    const dpr = window.devicePixelRatio || 1
    canvas.width = SIZE * dpr
    canvas.height = SIZE * dpr
    canvas.style.width = `${SIZE}px`
    canvas.style.height = `${SIZE}px`
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

    // Dark background
    ctx.fillStyle = '#111'
    ctx.fillRect(0, 0, SIZE, SIZE)

    // Draw image centered + offset
    const imgW = img.width * zoom
    const imgH = img.height * zoom
    const imgX = (SIZE - imgW) / 2 + offset.x
    const imgY = (SIZE - imgH) / 2 + offset.y
    ctx.drawImage(img, imgX, imgY, imgW, imgH)

    // Dim outside circle
    ctx.save()
    ctx.beginPath()
    ctx.rect(0, 0, SIZE, SIZE)
    ctx.arc(SIZE / 2, SIZE / 2, SIZE / 2 - 2, 0, Math.PI * 2, true)
    ctx.fillStyle = 'rgba(0,0,0,0.55)'
    ctx.fill()
    ctx.restore()

    // Circle border
    ctx.beginPath()
    ctx.arc(SIZE / 2, SIZE / 2, SIZE / 2 - 2, 0, Math.PI * 2)
    ctx.strokeStyle = 'rgba(255,255,255,0.5)'
    ctx.lineWidth = 2
    ctx.stroke()
  }, [zoom, offset])

  useEffect(() => {
    if (imgLoaded) draw()
  }, [imgLoaded, draw])

  // Drag handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    setDragging(true)
    setDragStart({ x: e.clientX - offset.x, y: e.clientY - offset.y })
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!dragging) return
    setOffset({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y })
  }

  const handleMouseUp = () => setDragging(false)

  const handleTouchStart = (e: React.TouchEvent) => {
    if (e.touches.length === 1) {
      setDragging(true)
      const t = e.touches[0]
      setDragStart({ x: t.clientX - offset.x, y: t.clientY - offset.y })
    }
  }

  const handleTouchMove = (e: React.TouchEvent) => {
    if (!dragging || e.touches.length !== 1) return
    const t = e.touches[0]
    setOffset({ x: t.clientX - dragStart.x, y: t.clientY - dragStart.y })
  }

  const handleTouchEnd = () => setDragging(false)

  // Crop and output
  const handleConfirm = () => {
    const img = imgRef.current
    if (!img) return

    const outCanvas = document.createElement('canvas')
    outCanvas.width = OUTPUT_SIZE
    outCanvas.height = OUTPUT_SIZE
    const ctx = outCanvas.getContext('2d')!

    const scale = OUTPUT_SIZE / SIZE
    const imgW = img.width * zoom * scale
    const imgH = img.height * zoom * scale
    const imgX = ((OUTPUT_SIZE - imgW) / 2) + offset.x * scale
    const imgY = ((OUTPUT_SIZE - imgH) / 2) + offset.y * scale

    ctx.beginPath()
    ctx.arc(OUTPUT_SIZE / 2, OUTPUT_SIZE / 2, OUTPUT_SIZE / 2, 0, Math.PI * 2)
    ctx.clip()
    ctx.drawImage(img, imgX, imgY, imgW, imgH)

    outCanvas.toBlob(blob => {
      if (blob) onConfirm(blob)
    }, 'image/png')
  }

  const modal = (
    <div className="avatar-crop-overlay" onClick={onCancel}>
      <div className="avatar-crop-modal" onClick={e => e.stopPropagation()}>
        <h3 className="avatar-crop-title">裁剪头像</h3>
        <canvas
          ref={canvasRef}
          className="avatar-crop-canvas"
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseUp}
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
        />
        <div className="avatar-crop-hint">拖拽移动 · 滑动缩放</div>
        <input
          type="range"
          className="avatar-crop-slider"
          min={0}
          max={100}
          step={1}
          value={sliderPct}
          onChange={e => handleSliderChange(parseInt(e.target.value))}
        />
        <div className="avatar-crop-actions">
          <button className="avatar-crop-btn avatar-crop-btn--cancel" onClick={onCancel}>取消</button>
          <button className="avatar-crop-btn avatar-crop-btn--confirm" onClick={handleConfirm}>确认</button>
        </div>
      </div>
    </div>
  )

  return createPortal(modal, document.body)
}

export default AvatarCropModal
