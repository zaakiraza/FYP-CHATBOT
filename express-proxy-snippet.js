/**
 * Optional: proxy chatbot API through Express so the browser talks to one origin.
 * Install: npm install http-proxy-middleware
 *
 * const { createProxyMiddleware } = require('http-proxy-middleware');
 *
 * app.use(
 *   '/chatbot-api',
 *   createProxyMiddleware({
 *     target: process.env.CHATBOT_PY_URL || 'http://127.0.0.1:8000',
 *     changeOrigin: true,
 *     pathRewrite: { '^/chatbot-api': '' },
 *   })
 * );
 *
 * Frontend then calls: fetch('/chatbot-api/api/therapist-chat', { ... })
 */
