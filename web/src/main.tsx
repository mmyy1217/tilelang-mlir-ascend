import React from 'react';
import ReactDOM from 'react-dom/client';
import { App } from './App';
import { HarnessApp } from './HarnessApp';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {new URLSearchParams(window.location.search).has('legacy') ? <App /> : <HarnessApp />}
  </React.StrictMode>
);
