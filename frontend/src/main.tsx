import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './style.css'

const root = document.getElementById('root') as HTMLElement
createRoot(root).render(
  <BrowserRouter>
    <App />
  </BrowserRouter>
)
