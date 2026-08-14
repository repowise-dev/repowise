public enum Disposition { OPEN, DECLINED, ACCEPTED }

public class DispositionManager { 
	private Disposition currentDisposition;
	public void updateDisposition(Disposition newDisposition) {
		currentDisposition = newDisposition;
	}
	public Disposition getDisposition() {
		return currentDisposition;
	}
}