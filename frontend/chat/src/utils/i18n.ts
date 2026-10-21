export type ChatLanguage = 'en' | 'es'

export function normalizeChatLanguage(value?: string | null): ChatLanguage {
  return value?.toLowerCase().startsWith('es') ? 'es' : 'en'
}

export function browserChatLanguage(): ChatLanguage {
  return normalizeChatLanguage(
    typeof navigator === 'undefined' ? 'en' : navigator.language,
  )
}

const COPY = {
  en: {
    approvalRequired: 'Approval required',
    confirmDeletion: 'Confirm deletion',
    reviewTransaction: 'Review transaction',
    approve: 'Approve',
    approveAndSave: 'Approve & save',
    confirmDelete: 'Confirm delete',
    cancel: 'Cancel',
    running: 'Running…',
    saving: 'Saving…',
    continue: 'Continue',
    send: 'Send',
    message: 'Message…',
    select: 'Select…',
    changes: 'Changes',
    items: 'Items to review',
    somethingWrong: 'Something went wrong',
    chatDisabled: 'Chat is disabled on the server.',
    thinking: [
      'Understanding your question…',
      'Checking your accounts…',
      'Running tools…',
      'Preparing response…',
    ],
  },
  es: {
    approvalRequired: 'Se requiere aprobación',
    confirmDeletion: 'Confirmar eliminación',
    reviewTransaction: 'Revisar transacción',
    approve: 'Aprobar',
    approveAndSave: 'Aprobar y guardar',
    confirmDelete: 'Confirmar eliminación',
    cancel: 'Cancelar',
    running: 'Procesando…',
    saving: 'Guardando…',
    continue: 'Continuar',
    send: 'Enviar',
    message: 'Mensaje…',
    select: 'Seleccionar…',
    changes: 'Cambios',
    items: 'Elementos para revisar',
    somethingWrong: 'Algo salió mal',
    chatDisabled: 'El chat está deshabilitado en el servidor.',
    thinking: [
      'Entendiendo tu pregunta…',
      'Revisando tus cuentas…',
      'Consultando información…',
      'Preparando la respuesta…',
    ],
  },
} as const

export function chatCopy(language: ChatLanguage) {
  return COPY[language]
}
