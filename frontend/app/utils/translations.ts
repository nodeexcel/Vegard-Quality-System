import nbTranslations from '../../files/nb.json'

const translations: Record<string, string> = nbTranslations

function lookupTranslation(key: string): string | undefined {
  if (!key) return undefined

  const direct = translations[key]
  if (direct) return direct

  const trimmed = key.trim()
  if (trimmed !== key && translations[trimmed]) return translations[trimmed]

  const lower = key.toLowerCase()
  if (lower !== key && translations[lower]) return translations[lower]

  const upper = key.toUpperCase()
  if (upper !== key && translations[upper]) return translations[upper]

  return undefined
}

/**
 * Translates rule IDs from English to Norwegian
 * Handles patterns like: D_LANGUAGE.long_sentences, E_METHOD.cost_speculation, A_ARKAT.konsekvens_unclear
 */
function translateRuleId(ruleId: string): string {
  if (!ruleId) return ruleId
  
  // Check if we have a direct translation
  const direct = lookupTranslation(ruleId)
  if (direct) {
    return direct
  }
  
  // Handle rule ID patterns: CATEGORY_SUBCATEGORY.rule_name
  const parts = ruleId.split('.')
  if (parts.length === 2) {
    const [prefix, ruleName] = parts
    const ruleNameTranslated = lookupTranslation(ruleName) || ruleName
    
    // Translate common subcategories
    let prefixTranslated = prefix
    if (prefix.includes('_LANGUAGE')) {
      prefixTranslated = prefix.replace('_LANGUAGE', '_SPRÅK')
    } else if (prefix.includes('_METHOD')) {
      prefixTranslated = prefix.replace('_METHOD', '_METODE')
    } else if (prefix.includes('_ARKAT')) {
      prefixTranslated = prefix.replace('_ARKAT', '_ARKAT')
    }
    
    return `${prefixTranslated}.${ruleNameTranslated}`
  }
  
  // Fallback: try to translate individual words
  return ruleId.split('_').map(word => lookupTranslation(word) || word).join('_')
}

function translateMatchExplain(text: string): string | null {
  const match = text.match(/^Matched '(.+)' on page (\d+)\.$/)
  if (match) {
    return `Fant '${match[1]}' på side ${match[2]}.`
  }

  return null
}

/**
 * Translates an English engine/UI value to Norwegian (Bokmål)
 * @param key - The English value to translate
 * @returns The Norwegian translation if found, otherwise returns the original key
 */
export function translate(key: string | null | undefined): string {
  if (!key) return key || ''

  const matchExplain = translateMatchExplain(key)
  if (matchExplain) {
    return matchExplain
  }

  // Check for direct translation first
  const direct = lookupTranslation(key)
  if (direct) {
    return direct
  }
  
  // If it looks like a rule ID, use rule translation
  if (key.includes('.') && /^[A-E]_/.test(key)) {
    return translateRuleId(key)
  }
  
  return key
}
