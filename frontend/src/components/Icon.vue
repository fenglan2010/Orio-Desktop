<template>
  <svg
    class="ico"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.75"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path v-if="name === 'logo'" d="M4 16c2-6 4-9 8-9s6 3 8 9" stroke="#fff" stroke-width="2" />
    <circle v-if="name === 'logo'" cx="12" cy="16" r="1.4" fill="#fff" stroke="none" />

    <template v-else-if="name === 'monitor'">
      <path d="M4 19h16M6 17V7a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v10" />
      <path d="M8 13l2.5-2.5 2 2L16 9" />
    </template>
    <template v-else-if="name === 'trend'">
      <path d="M4 19h16M5 15l5-5 3 3 6-7" />
    </template>
    <template v-else-if="name === 'play'">
      <circle cx="12" cy="12" r="9" />
      <path d="M10 8.5v7l6-3.5-6-3.5z" fill="currentColor" stroke="none" />
    </template>
    <template v-else-if="name === 'target'">
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
    </template>
    <template v-else-if="name === 'spark'">
      <path d="M12 3l1.6 5.2L19 10l-5.4 1.8L12 17l-1.6-5.2L5 10l5.4-1.8L12 3z" />
      <path d="M18 15l.7 2.2L21 18l-2.3.8L18 21l-.7-2.2L15 18l2.3-.8L18 15z" />
    </template>
    <template v-else-if="name === 'settings'">
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3v2M12 19v2M4.9 6.3l1.5 1.5M17.6 16.2l1.5 1.5M3 12h2M19 12h2M4.9 17.7l1.5-1.5M17.6 7.8l1.5-1.5" />
    </template>
    <template v-else-if="name === 'help'">
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9a2.5 2.5 0 1 1 3.6 2.3c-.8.4-1.1 1-1.1 1.7V14" />
      <circle cx="12" cy="17" r=".8" fill="currentColor" stroke="none" />
    </template>
    <template v-else-if="name === 'log'">
      <path d="M7 4h10a2 2 0 0 1 2 2v14l-3-2-3 2-3-2-3 2V6a2 2 0 0 1 2-2z" />
      <path d="M9 9h6M9 13h6" />
    </template>
    <template v-else-if="name === 'feedback'">
      <path d="M5 5h14v10H8l-3 3V5z" />
    </template>
    <template v-else-if="name === 'search'">
      <circle cx="11" cy="11" r="6" />
      <path d="M16 16l4 4" />
    </template>
    <template v-else-if="name === 'calendar'">
      <rect x="4" y="5" width="16" height="15" rx="2" />
      <path d="M8 3v4M16 3v4M4 10h16" />
    </template>
    <template v-else-if="name === 'building'">
      <path d="M4 20h16M6 20V6l6-3 6 3v14M10 20v-5h4v5" />
      <path d="M9 9h.01M15 9h.01M9 13h.01M15 13h.01" />
    </template>
    <template v-else-if="name === 'bell'">
      <path d="M6 9a6 6 0 1 1 12 0c0 7 2 7 2 9H4c0-2 2-2 2-9" />
      <path d="M10 20a2 2 0 0 0 4 0" />
    </template>
    <template v-else-if="name === 'gear'">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9c.3.6.9 1 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
    </template>
    <template v-else-if="name === 'chat'">
      <path d="M5 6h14v9H8l-3 3V6z" />
      <path d="M8 10h8M8 13h5" />
    </template>
    <template v-else-if="name === 'home'">
      <path d="M4 11l8-7 8 7v9a1 1 0 0 1-1 1h-5v-6H10v6H5a1 1 0 0 1-1-1v-9z" />
    </template>
    <template v-else-if="name === 'refresh'">
      <path d="M20 12a8 8 0 1 1-2.2-5.5" />
      <path d="M20 5v5h-5" />
    </template>
    <template v-else-if="name === 'chevron-left'">
      <path d="M14 6l-6 6 6 6" />
    </template>
    <template v-else-if="name === 'chevron-right'">
      <path d="M10 6l6 6-6 6" />
    </template>
    <template v-else-if="name === 'chevron-down'">
      <path d="M6 10l6 6 6-6" />
    </template>
    <template v-else-if="name === 'filter'">
      <path d="M4 6h16M7 12h10M10 18h4" />
    </template>
    <template v-else-if="name === 'download'">
      <path d="M12 4v12M7 11l5 5 5-5M5 20h14" />
    </template>
    <template v-else-if="name === 'user'">
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 19a7 7 0 0 1 14 0" />
    </template>
    <template v-else-if="name === 'users'">
      <circle cx="9" cy="8" r="3" />
      <path d="M3 19a6 6 0 0 1 12 0" />
      <circle cx="17" cy="9" r="2.5" />
      <path d="M16 19a5 5 0 0 0 5-5" />
    </template>
    <template v-else-if="name === 'link'">
      <path d="M10 13a5 5 0 0 0 7.07 0l1.4-1.4a5 5 0 0 0-7.07-7.07L10 6" />
      <path d="M14 11a5 5 0 0 0-7.07 0L5.5 12.4a5 5 0 0 0 7.07 7.07L14 18" />
    </template>
    <template v-else-if="name === 'pms'">
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <path d="M8 9h8M8 13h5" />
    </template>
    <template v-else-if="name === 'shield'">
      <path d="M12 3l8 3v6c0 5-3.4 7.8-8 9-4.6-1.2-8-4-8-9V6l8-3z" />
    </template>
    <template v-else-if="name === 'bell-off'">
      <path d="M6 9a6 6 0 0 1 12 0c0 7 2 7 2 9H4c0-2 2-2 2-9" />
    </template>
    <template v-else-if="name === 'palette'">
      <path d="M12 4a8 8 0 1 0 0 16h2a2 2 0 0 0 0-4h-1" />
      <circle cx="8" cy="10" r=".9" fill="currentColor" stroke="none" />
      <circle cx="10.5" cy="7.5" r=".9" fill="currentColor" stroke="none" />
      <circle cx="14.5" cy="7.5" r=".9" fill="currentColor" stroke="none" />
    </template>
    <template v-else-if="name === 'close'">
      <path d="M6 6l12 12M18 6L6 18" />
    </template>
    <template v-else-if="name === 'check'">
      <path d="M5 12l5 5 9-10" />
    </template>
    <template v-else-if="name === 'dollar'">
      <path d="M12 4v16M16 8c0-2-1.8-3.5-4-3.5S8 6 8 8s1.5 3 4 3.5 4 1.5 4 3.5-1.8 3.5-4 3.5-4-1.5-4-3.5" />
    </template>
    <template v-else-if="name === 'box'">
      <path d="M4 8l8-4 8 4v8l-8 4-8-4V8z" />
      <path d="M12 12v8M4 8l8 4 8-4" />
    </template>
    <template v-else-if="name === 'tag'">
      <path d="M3 12l9-9h6l3 3v6l-9 9-9-9z" />
      <circle cx="15" cy="7" r="1" />
    </template>
    <template v-else-if="name === 'mail'">
      <rect x="4" y="6" width="16" height="12" rx="2" />
      <path d="M4 8l8 5 8-5" />
    </template>
    <template v-else-if="name === 'sun'">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 3v2M12 19v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M3 12h2M19 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" />
    </template>
    <template v-else-if="name === 'moon'">
      <path d="M17 13.5A7 7 0 1 1 10.5 6 5.5 5.5 0 0 0 17 13.5z" />
    </template>
    <template v-else-if="name === 'auto'">
      <rect x="5" y="6" width="14" height="10" rx="2" />
      <path d="M8 20h8M12 16v4" />
    </template>
    <path v-else d="M5 12h14" />
  </svg>
</template>
<script setup>
defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 18 },
});
</script>
