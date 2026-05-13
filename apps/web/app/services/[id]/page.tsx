import { ServiceDetailConsole } from '@/components/admin/service-detail-console';

export default async function ServiceDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ServiceDetailConsole serviceId={id} />;
}
