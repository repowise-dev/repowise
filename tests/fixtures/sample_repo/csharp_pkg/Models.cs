namespace Sample.Models
{
    public enum Operation
    {
        Add,
        Subtract,
        Multiply,
        Divide
    }

    public class CalculationRecord
    {
        public Operation Op { get; set; }
        public double X { get; set; }
        public double Y { get; set; }
        public double Result { get; set; }
    }

    public interface IComputable
    {
        double Compute();
    }
}
