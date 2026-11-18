import useCachedQueries from './useCachedQueries'
export default function useMarketSeries(cache, queries) {
  return useCachedQueries(cache, '/trade/trends', queries)
}
