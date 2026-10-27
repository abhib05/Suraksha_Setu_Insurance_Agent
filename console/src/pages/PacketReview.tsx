import { useParams } from 'react-router-dom'

// M6: packet preview with rule citations and verifier findings beside each field;
// approve / return send the workflow signals.
export default function PacketReview() {
  const { claimId } = useParams()
  return (
    <section>
      <h1>Packet {claimId}</h1>
      <p className="muted">Field-by-field review with sources arrives in M6.</p>
    </section>
  )
}
