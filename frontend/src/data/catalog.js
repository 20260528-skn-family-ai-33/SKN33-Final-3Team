// Reference data is populated by the API. No sample country/product records are used.
export const hs4Groups = []
export const products = []
export const countries = []
export const continents = []
export const exchangeRates = []
export const registerCountries = items => countries.splice(0, countries.length, ...items.map(c => ({ ...c, name: c.name_ko, currency: c.currency_code })))
export function registerProducts(items) {
  for (const h of items) if (!products.some(p => p.id === h.hs_code)) products.push({ id: h.hs_code, name: h.name_ko, hs4: h.hs_code.slice(0, 4) })
}
export const countryCode = name => countries.find(c => c.name === name)?.country_code || name
export const countryName = code => countries.find(c => c.country_code === code)?.name || code
export const productName = id => products.find(p => p.id === id)?.name || id || '전체 품목'
export const productKeyword = productName
export const toggleValue = (list, value) => list.includes(value) ? list.filter(v => v !== value) : [...list, value]
